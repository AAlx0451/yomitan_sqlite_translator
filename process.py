import argparse
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path


FILES_TO_SKIP = ('index.json',)
PREFIXES_TO_SKIP = ('tag_bank',)

def create_database(db_path: Path) -> sqlite3.Connection:
    """Создает новую базу данных SQLite и таблицу 'translations'."""
    print(f"Создание новой базы данных в: {db_path}")
    try:
        # Устанавливаем соединение. Если файла нет, он будет создан.
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        # Схема таблицы осталась прежней
        cur.execute('''
            CREATE TABLE translations (
                word TEXT,
                reading TEXT,
                kind TEXT,
                english TEXT,
                priority INTEGER
            )
        ''')
        con.commit()
        return con
    except sqlite3.Error as e:
        print(f"Ошибка при создании базы данных: {e}", file=sys.stderr)
        sys.exit(1)

def import_dictionary_from_zip(zip_path: Path, con: sqlite3.Connection):
    """
    Извлекает данные из одного ZIP-архива словаря и загружает их в базу данных.
    Все вставки выполняются в рамках одной транзакции для максимальной производительности.
    """
    print(f"Начинается импорт из архива: {zip_path.name}...")
    
    # Создаем курсор один раз для всего процесса импорта из файла
    cur = con.cursor()
    total_entries_added = 0

    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_f:
            for name in zip_f.namelist():
                # Пропускаем служебные файлы по имени или префиксу
                if name in FILES_TO_SKIP or name.startswith(PREFIXES_TO_SKIP):
                    continue

                # Используем 'with' для автоматического закрытия файла
                with zip_f.open(name) as f:
                    # json.load работает с файловыми объектами напрямую
                    entries = json.load(f)
                    
                    # Готовим данные для массовой вставки (executemany)
                    to_insert = []
                    for entry in entries:
                        # Используем срезы для надежности, даже если в записи больше 6 элементов
                        word, reading, kind, _, priority, translation = entry[:6]
                        
                        # Собираем кортеж для вставки
                        # '; '.join(translation) - объединяет список переводов в одну строку
                        to_insert.append(
                            (word, reading, kind, '; '.join(translation), int(priority))
                        )
                    
                    if to_insert:
                        # executemany - самый эффективный способ вставить много записей
                        cur.executemany(
                            'INSERT INTO translations VALUES (?, ?, ?, ?, ?)',
                            to_insert
                        )
                        total_entries_added += len(to_insert)

        # con.commit() вызывается ОДИН РАЗ после обработки ВСЕХ файлов в архиве.
        # Это ключ к высокой производительности.
        con.commit()
        print(f"Успешно импортировано {total_entries_added} записей из {zip_path.name}.")

    except (zipfile.BadZipFile, json.JSONDecodeError) as e:
        print(f"Ошибка при обработке файла {zip_path.name}: {e}", file=sys.stderr)
        # Откатываем транзакцию, если что-то пошло не так с этим архивом
        con.rollback()
    except sqlite3.Error as e:
        print(f"Ошибка базы данных при импорте из {zip_path.name}: {e}", file=sys.stderr)
        con.rollback()


def main():
    """
    Главная функция: парсинг аргументов командной строки и управление процессом.
    """
    parser = argparse.ArgumentParser(
        description="Импортер словарей в формате JMDict (zip) в базу данных SQLite.",
        formatter_class=argparse.RawTextHelpFormatter # для красивого отображения help
    )
    parser.add_argument(
        "dictionary_files",
        metavar="ZIP_FILE",
        type=Path,
        nargs='+',  # '+' означает "один или больше" аргументов
        help="Путь к одному или нескольким ZIP-архивам словарей для импорта."
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("dict.db"), # По умолчанию файл создается в текущей директории
        help="Путь для сохранения итогового файла базы данных (по умолчанию: dict.db)."
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true", # Если флаг есть, то True, иначе False
        help="Перезаписать существующий файл базы данных без подтверждения."
    )

    args = parser.parse_args()
    db_path = args.output

    # Проверка, существует ли файл БД и не указан ли флаг --force
    if db_path.exists() and not args.force:
        print(
            f"Файл базы данных '{db_path}' уже существует.\n"
            "Используйте флаг -f или --force, чтобы перезаписать его.",
            file=sys.stderr
        )
        sys.exit(1)
    
    # Безопасное удаление старого файла, если он есть
    if db_path.exists():
        try:
            os.remove(db_path)
            print(f"Старый файл '{db_path}' удален.")
        except OSError as e:
            print(f"Не удалось удалить старый файл '{db_path}': {e}", file=sys.stderr)
            sys.exit(1)

    # Создаем БД и получаем объект соединения
    con = create_database(db_path)
    
    # Используем 'with con:' для автоматического управления транзакциями и закрытия соединения
    try:
        with con:
            # Итерируемся по всем файлам словарей, переданным в командной строке
            for zip_file in args.dictionary_files:
                if not zip_file.is_file():
                    print(f"Внимание: файл не найден, пропуск: {zip_file}", file=sys.stderr)
                    continue
                import_dictionary_from_zip(zip_file, con)
            
            print("Создание индекса для ускорения поиска...")
            # Индекс создается один раз после вставки всех данных - это самый быстрый способ
            con.cursor().execute('CREATE INDEX translations_index ON translations(word)')
            print("Индекс успешно создан.")

    except Exception as e:
        print(f"Произошла непредвиденная ошибка: {e}", file=sys.stderr)
        sys.exit(1)

    print("\nВсе словари успешно импортированы!")
    print(f"Итоговая база данных сохранена в: {db_path.resolve()}")


if __name__ == "__main__":
    main()
