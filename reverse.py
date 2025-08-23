import json
import os
import sqlite3
import zipfile
from collections import defaultdict

def export_sqlite_to_yomitan(db_path, original_zip_path, output_zip_path, translation_column='russian'):
    """
    Универсальный экспортер из SQLite в Yomitan-совместимый zip-архив.
    """
    print(f"Подключение к базе данных: {db_path}")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    print(f"Создание нового архива: {output_zip_path}")
    with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as new_zip:
        print("Копирование служебных файлов (index.json, tag_bank*)...")
        with zipfile.ZipFile(original_zip_path, 'r') as original_zip:
            for filename in original_zip.namelist():
                if not filename.startswith('term_bank_'):
                    content = original_zip.read(filename)
                    new_zip.writestr(filename, content)

        print("Чтение и группировка записей из базы данных...")
        cur.execute(f"SELECT word, reading, kind, priority, {translation_column}, source_file, original_index FROM dictionary ORDER BY source_file, original_index")
        
        entries_by_file = defaultdict(list)
        for row in cur.fetchall():
            translation_list = row[translation_column].split('; ') if row[translation_column] else []
            new_entry = [row['word'], row['reading'], row['kind'], "", int(row['priority']), translation_list]
            entries_by_file[row['source_file']].append(new_entry)

        print("Запись восстановленных term_bank файлов в архив...")
        for filename, entries in sorted(entries_by_file.items()):
            print(f"  -> Запись {filename} ({len(entries)} записей)")
            json_data = json.dumps(entries, ensure_ascii=False, separators=(',', ':'))
            new_zip.writestr(filename, json_data)

    con.close()
    print(f"\nГотово! Архив {os.path.basename(output_zip_path)} успешно создан.")


# --- НАСТРОЙКА ПУТЕЙ ---
directory = os.path.dirname(__file__)
# <--- ИЗМЕНЕНО: Имя базы данных возвращено на dict.db
db_file = os.path.join(os.path.dirname(directory), "dict.db") 
original_zip = os.path.join(directory, 'dict.zip')
output_zip = os.path.join(directory, 'dict_trans.zip')
translation_column_name = 'russian' 

if not os.path.exists(db_file):
    print(f"Ошибка: Не найдена база данных {db_file}")
elif not os.path.exists(original_zip):
     print(f"Ошибка: Не найден оригинальный архив {original_zip}")
else:
    export_sqlite_to_yomitan(db_file, original_zip, output_zip, translation_column_name)
