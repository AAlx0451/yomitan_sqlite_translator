#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# --- Константы конфигурации ---

# Количество записей в одном файле term_bank_*.json.
# 10000 — стандартное значение для многих словарей Yomitan.
CHUNK_SIZE = 10000

# Имя таблицы и колонок в базе данных SQLite.
# Измените их, если ваша структура БД отличается.
DB_TABLE_NAME = "translations"
DB_COLUMNS = ["word", "reading", "kind", "english", "priority"]


def create_index_json(title: str, description: str, author: str) -> dict:
    """Генерирует словарь для файла index.json."""
    revision = f"db_export_{datetime.now().strftime('%Y.%m.%d_%H%M%S')}"
    return {
        "title": title,
        "format": 3,
        "revision": revision,
        "sequenced": True,
        "description": description,
        "author": author,
    }


def export_to_yomitan_zip(db_path: Path, zip_path: Path, title: str):
    """
    Экспортирует данные из базы данных SQLite в ZIP-архив формата Yomitan.
    """
    if not db_path.is_file():
        print(f"Ошибка: Файл базы данных не найден: {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Подключение к базе данных: {db_path}")
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.cursor()
        sql_query = f"SELECT {', '.join(DB_COLUMNS)} FROM {DB_TABLE_NAME}"
        cur.execute(sql_query)
    except sqlite3.OperationalError as e:
        print(
            f"Ошибка при запросе к базе данных: {e}\n"
            f"Убедитесь, что таблица '{DB_TABLE_NAME}' и колонки {DB_COLUMNS} существуют.",
            file=sys.stderr
        )
        sys.exit(1)
    except sqlite3.Error as e:
        print(f"Ошибка при чтении из базы данных: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Начинается экспорт в архив: {zip_path}...")
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_f:
            # 1. Записываем index.json
            index_content = create_index_json(
                title=title,
                description=f"Словарь '{title}', экспортированный из базы данных SQLite.",
                author="DB to Yomitan Exporter"
            )
            # ensure_ascii=False для корректной записи кириллицы и каны
            zip_f.writestr(
                "index.json",
                json.dumps(index_content, ensure_ascii=False, indent=2)
            )

            # 2. Записываем пустой tag_bank_1.json (обязательный файл)
            zip_f.writestr("tag_bank_1.json", "[]")

            # 3. Обрабатываем и записываем словарные статьи (term banks)
            bank_num = 1
            entries_in_current_bank = []
            total_entries_exported = 0

            for row in cur:
                word, reading, kind, english_str, priority = row

                # Предполагается, что переводы разделены "; "
                translations = english_str.split('; ')
                
                # Формируем запись в формате Yomitan:
                # [термин, чтение, теги, правила, счетчик популярности, определения]
                yomitan_entry = [word, reading, kind, "", int(priority), translations]
                entries_in_current_bank.append(yomitan_entry)
                total_entries_exported += 1

                if len(entries_in_current_bank) >= CHUNK_SIZE:
                    filename = f"term_bank_{bank_num}.json"
                    print(f"  -> Запись файла {filename} ({len(entries_in_current_bank)} записей)...")
                    zip_f.writestr(
                        filename,
                        json.dumps(entries_in_current_bank, ensure_ascii=False, separators=(',', ':'))
                    )
                    bank_num += 1
                    entries_in_current_bank = []

            # Записываем последний (неполный) банк, если он есть
            if entries_in_current_bank:
                filename = f"term_bank_{bank_num}.json"
                print(f"  -> Запись файла {filename} ({len(entries_in_current_bank)} записей)...")
                zip_f.writestr(
                    filename,
                    json.dumps(entries_in_current_bank, ensure_ascii=False, separators=(',', ':'))
                )

        print(f"✅ Успешно экспортировано {total_entries_exported} записей.")

    except (IOError, zipfile.BadZipFile) as e:
        print(f"Ошибка при записи в ZIP-архив: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()


def main():
    """Парсит аргументы командной строки и запускает процесс экспорта."""
    parser = argparse.ArgumentParser(
        description="Экспортер базы данных SQLite в словарь формата Yomitan (zip).",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "database_file",
        metavar="DB_FILE",
        type=Path,
        help="Путь к файлу базы данных SQLite для экспорта (например, dict.db)."
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        required=True,
        help="Путь для сохранения итогового ZIP-архива словаря (например, my_dictionary.zip)."
    )
    parser.add_argument(
        "-t", "--title",
        type=str,
        default="Exported Dictionary",
        help="Название словаря, которое будет отображаться в Yomitan."
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Перезаписать существующий ZIP-архив без подтверждения."
    )

    args = parser.parse_args()
    db_path = args.database_file
    zip_path = args.output

    if zip_path.exists() and not args.force:
        print(
            f"Ошибка: Файл '{zip_path}' уже существует.\n"
            "Используйте флаг -f или --force, чтобы перезаписать его.",
            file=sys.stderr
        )
        sys.exit(1)

    export_to_yomitan_zip(db_path, zip_path, args.title)

    print("\n🎉 Экспорт успешно завершен!")
    print(f"Итоговый файл словаря: {zip_path.resolve()}")


if __name__ == "__main__":
    main()
