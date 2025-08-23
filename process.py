import json
import os
import sqlite3
import zipfile

def import_yomitan_to_sqlite(zip_path, db_path):
    """
    Универсальный импортер Yomitan-словарей в SQLite.
    Сохраняет структурные метаданные (имя файла и индекс) для каждой записи.
    """
    if os.path.exists(db_path):
        print(f"Удаление старой базы данных: {db_path}")
        os.remove(db_path)

    print(f"Создание новой базы данных: {db_path}")
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # --- Новая структура таблицы ---
    # Добавлены source_file и original_index для сохранения структуры
    # Добавлен PRIMARY KEY для более надежной идентификации
    cur.execute('''CREATE TABLE dictionary (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   word TEXT,
                   reading TEXT,
                   kind TEXT,
                   english TEXT,
                   priority INTEGER,
                   source_file TEXT NOT NULL,
                   original_index INTEGER NOT NULL
                )''')
    con.commit()

    print(f"Чтение архива: {zip_path}")
    with zipfile.ZipFile(zip_path, 'r') as zip_file:
        # Получаем список файлов term_bank, отсортированных для порядка
        term_bank_files = sorted([name for name in zip_file.namelist() if name.startswith('term_bank_')])

        for filename in term_bank_files:
            print(f"Обработка файла: {filename}...")
            with zip_file.open(filename) as f:
                entries = json.load(f)
            
            to_insert = []
            # Используем enumerate для получения индекса каждой записи
            for index, entry in enumerate(entries):
                # Стандартная структура Yomitan term entry
                word, reading, kind, _, priority, translation = entry[:6]
                
                # Сохраняем перевод как строку, разделенную '; '
                translation_str = '; '.join(translation)

                # Добавляем метаданные в кортеж для вставки
                to_insert.append((word, reading, kind, translation_str, int(priority), filename, index))

            if to_insert:
                cur.executemany(
                    'INSERT INTO dictionary (word, reading, kind, english, priority, source_file, original_index) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    to_insert
                )
    
    print("Создание индексов для ускорения поиска...")
    cur.execute('CREATE INDEX idx_word ON dictionary(word)')
    cur.execute('CREATE INDEX idx_source ON dictionary(source_file, original_index)')
    
    con.commit()
    con.close()
    print("\nИмпорт успешно завершен!")


# --- НАСТРОЙКА ПУТЕЙ ---
directory = os.path.dirname(__file__)
source_zip = os.path.join(directory, 'dict.zip')
# Рекомендую назвать новую базу данных по-другому, чтобы не путать
db_file = os.path.join(os.path.dirname(directory), "dict.db") 

if not os.path.exists(source_zip):
    print(f"Ошибка: Не найден исходный архив {source_zip}")
else:
    import_yomitan_to_sqlite(source_zip, db_file)
