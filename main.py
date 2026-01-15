from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.image import Image
from kivy.core.image import Image as CoreImage

import sys
import os
import json
import time
from datetime import datetime, timedelta
import shutil

# Твои модули (должны лежать рядом с main.py)
from emulator_core import parameters_known, set_tty, get_version_core
from determine_basal import get_version_determine_basal

# Для Android SAF + Intent
from jnius import autoclass, cast
from android.activity import bind_on_activity_result
from android.permissions import request_permissions, Permission, check_permission
from kivy.utils import platform

Intent = autoclass('android.content.Intent')
Uri = autoclass('android.net.Uri')
PythonActivity = autoclass('org.kivy.android.PythonActivity')
ContentResolver = autoclass('android.content.ContentResolver')
activity = PythonActivity.mActivity

class EmulatorApp(App):
    def build(self):
        self.layout = BoxLayout(
            orientation='vertical',
            padding=[20, 80, 20, 20],  # Увеличен отступ сверху
            spacing=10
        )

        self.status = Label(
            text='Нажмите "Выбрать файлы" и выберите минимум 3 файла\n(log, vdf/dat, config)',
            size_hint_y=0.5,
            text_size=(None, None),
            halign='center',
            valign='middle',
            markup=True,
            padding=[0, 40, 0, 0]  # Дополнительный отступ внутри Label
        )

        btn_choose = Button(text='Выбрать файлы (.log, .vdf/.dat, .config)')
        btn_choose.bind(on_press=self.show_file_chooser)

        self.btn_run = Button(text='Запустить эмуляцию', disabled=True)
        self.btn_run.bind(on_press=self.run_emulation)

        self.btn_chart = Button(text='Показать графики', disabled=True)
        self.btn_chart.bind(on_press=self.show_charts)

        self.layout.add_widget(self.status)
        self.layout.add_widget(btn_choose)
        self.layout.add_widget(self.btn_run)
        self.layout.add_widget(self.btn_chart)

        self.results = []
        self.log_path = None
        self.vdf_path = None
        self.config_path = None

        # Регистрация обработчика результатов Intent
        if platform == 'android':
            bind_on_activity_result(self.on_activity_result)
            self.status.text += '\n[b]Обработчик SAF зарегистрирован[/b]'

        return self.layout

    def show_file_chooser(self, instance):
        self.status.text = '[color=ff0000]Открывается системный выбор файлов...[/color]'

        if platform != 'android':
            self.status.text = 'Функция только для Android'
            return

        intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        intent.setType("*/*")
        intent.putExtra(Intent.EXTRA_MIME_TYPES, ["text/plain", "application/octet-stream"])
        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, True)

        activity.startActivityForResult(intent, 1001)
        self.status.text += '\n[b]Ждём выбор файлов...[/b]'

    def on_activity_result(self, request_code, result_code, data):
        self.status.text = (
            f'[b]Получен результат Intent[/b]\n'
            f'request_code = {request_code}\n'
            f'result_code = {result_code}\n'
            f'data = {data}\n'
        )

        if request_code != 1001:
            self.status.text += '\nНеверный request_code'
            return

        if result_code != -1:
            self.status.text += '\nРезультат не OK (отменено)'
            return

        if data is None:
            self.status.text += '\ndata is None — ничего не выбрано'
            return

        uris = []
        clip_data = data.getClipData()
        if clip_data is not None:
            self.status.text += f'\nВыбрано через ClipData: {clip_data.getItemCount()} файлов'
            for i in range(clip_data.getItemCount()):
                uri = clip_data.getItemAt(i).getUri()
                uris.append(uri)
        else:
            uri = data.getData()
            if uri:
                self.status.text += '\nВыбрано один файл через getData()'
                uris.append(uri)
            else:
                self.status.text += '\nURI не найден в data'
                return

        if len(uris) < 3:
            self.status.text += f'\nВыбрано только {len(uris)} файлов. Нужно минимум 3.'
            self.btn_run.disabled = True
            return

        app_dir = self.user_data_dir
        self.status.text += f'\n[b]Копируем файлы в:[/b] {app_dir}'

        try:
            self.log_path = self._copy_uri_to_file(uris[0], os.path.join(app_dir, 'AndroidAPS.log'))
            self.vdf_path = self._copy_uri_to_file(uris[1], os.path.join(app_dir, 'profile.vdf'))
            self.config_path = self._copy_uri_to_file(uris[2], os.path.join(app_dir, 'emulator.config'))

            self.status.text = (
                f'[color=00ff00][b]УСПЕХ![/b][/color]\n'
                f'Файлы скопированы:\n'
                f'• Log: {os.path.basename(self.log_path)}\n'
                f'• VDF: {os.path.basename(self.vdf_path)}\n'
                f'• Config: {os.path.basename(self.config_path)}\n\n'
                f'Нажмите "Запустить эмуляцию"'
            )
            self.btn_run.disabled = False
        except Exception as e:
            self.status.text += f'\n[color=ff0000]Ошибка копирования:[/color]\n{str(e)}'
            self.btn_run.disabled = True

    def _copy_uri_to_file(self, uri, dest_path):
        self.status.text += f'\nКопирую: {uri.toString()} → {dest_path}'
        content_resolver = activity.getContentResolver()
        input_stream = content_resolver.openInputStream(uri)
        if input_stream is None:
            raise Exception(f"Не удалось открыть поток для URI: {uri}")
        try:
            with open(dest_path, 'wb') as f:
                shutil.copyfileobj(input_stream, f)
        finally:
            input_stream.close()
        return dest_path

    def run_emulation(self, instance):
        if not all([self.log_path, self.vdf_path, self.config_path]):
            self.status.text = '[color=ff0000]Не все файлы скопированы[/color]'
            return

        self.results = []
        self.status.text = 'Запуск эмуляции...'

        # ────────────────────────────────────────────────────────────────
        # ПОЛНАЯ ЛОГИКА ЭМУЛЯЦИИ ИЗ emulator_batch.py
        # ────────────────────────────────────────────────────────────────

        vdf_dir = os.path.dirname(self.log_path) + '/'

        # Чтение config
        my_decimal = '.'
        pickExtraCarbs = []
        pickMoreSMB = []
        pickLessSMB = []

        try:
            with open(self.config_path, 'r') as cfg:
                next_row = 'extraCarbs'
                for zeile in cfg:
                    key = zeile[:1]
                    if key == '[':
                        List = []
                        wo = zeile.find(']')
                        eleList = zeile[1:wo].split(',')
                        if '' not in eleList:
                            for i in range(len(eleList)):
                                List.append(eval(eleList[i]))
                    else:
                        wo = zeile.find('}')
                        zeile = zeile[:wo+1]

                    if next_row == 'extraCarbs':
                        pickExtraCarbs = List
                        next_row = 'extraBolus'
                    elif next_row == 'extraBolus':
                        pickMoreSMB = List
                        next_row = 'lessBolus'
                    elif next_row == 'lessBolus':
                        pickLessSMB = List
                        next_row = 'outputs'
                    elif next_row == 'outputs':
                        arg2 = 'Android/' + my_decimal
                        outputJson = json.loads(zeile)
                        total_width = 6
                        for ele in outputJson:
                            width = outputJson[ele]
                            if width > 0:
                                arg2 += '/' + ele
                                total_width += width
                        next_row = 'end'
        except Exception as e:
            self.status.text = f'[color=ff0000]Ошибка config:[/color]\n{str(e)}'
            return

        # Настройки
        myseek = self.log_path
        varFile = self.vdf_path
        t_startLabel = '2000-00-00T00:00:00Z'
        t_stoppLabel = '2099-00-00T00:00:00Z'

        echo_msg = {}
        echo_msg = get_version_batch(echo_msg)
        echo_msg = get_version_core(echo_msg)
        echo_msg = get_version_determine_basal(echo_msg)

        entries = {}
        lastTime = '0'
        wdhl = 'n'  # 'n' — один проход; для цикла — 'y'

        while wdhl == 'y':
            try:
                loopInterval, thisTime, extraSMB, CarbReqGram, CarbReqTime, lastCOB, fn_first = parameters_known(
                    myseek, arg2, varFile, t_startLabel, t_stoppLabel, entries, "", my_decimal
                )

                if thisTime in ['SYNTAX', 'UTF8']:
                    self.status.text = f'[color=ff0000]Ошибка:[/color] {thisTime}'
                    return

                self.results.append({
                    'Time': thisTime,
                    'Extra SMB': float(extraSMB or 0),
                    'Carb Req Gram': float(CarbReqGram or 0),
                    'Carb Req Time': CarbReqTime or '',
                    'Last COB': float(lastCOB or 0),
                    # Добавь другие поля из entries, если они есть
                })

                lastTime = thisTime
                wdhl = 'n'  # один проход

            except Exception as e:
                self.status.text = f'[color=ff0000]Ошибка эмуляции:[/color]\n{str(e)}'
                return

        # Сохранение результатов
        if self.results:
            df = pd.DataFrame(self.results)
            save_path = os.path.join(self.user_data_dir, 'aaps_results.xlsx')
            try:
                df.to_excel(save_path, index=False)
                self.status.text = (
                    f'[color=00ff00][b]Готово![/b][/color]\n'
                    f'Результаты сохранены:\n{save_path}'
                )
                self.btn_chart.disabled = False
            except Exception as e:
                self.status.text = f'[color=ff0000]Ошибка Excel:[/color]\n{str(e)}'
        else:
            self.status.text = '[color=ff0000]Нет данных после эмуляции[/color]'

    def show_charts(self, instance):
        if not self.results:
            self.status.text = 'Нет данных'
            return

        df = pd.DataFrame(self.results)

        fig, ax = plt.subplots(2, 1, figsize=(10, 10))
        ax[0].plot(df['Time'], df['Extra SMB'], label='Extra SMB', color='red')
        ax[0].legend()
        ax[1].bar(df['Time'], df['Carb Req Gram'], label='Carb Req (г)', color='purple')
        ax[1].legend()

        buf = BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)

        img = CoreImage(buf, ext='png')
        popup = Popup(title='Графики эмуляции', content=Image(texture=img.texture), size_hint=(0.9, 0.9))
        popup.open()

EmulatorApp().run()