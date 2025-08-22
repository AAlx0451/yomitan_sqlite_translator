#!/bin/bash
set -euo pipefail
IFS=$'\n\t'

# ==============================================================================
# =                  Gemini SQLite Batch Translator (v5.0)                     =
# ==============================================================================

# --- Настройки ---
DB_FILE="${HOME}/dict.db"
TABLE_NAME="translations"
SRC_COLUMN="english"
DST_COLUMN="russian"
PK_COLUMN="rowid"

BATCH_SIZE=300
MODEL="gemini-2.5-flash-lite"      # Модель по умолчанию
SRC_LANG="English"
DST_LANG="Russian"

BASE_API_URL="https://generativelanguage.googleapis.com/v1beta/models/"
CURRENT_API_KEY="${GOOGLE_API_KEY:-}"

AVAILABLE_MODELS=(
  "gemini-2.5-flash"
  "gemini-2.5-flash-lite"
  "gemini-2.5-pro"
  "gemini-2.0-flash"
  "gemini-1.5-flash"
)

# --- Цвета ---
declare -A COLORS=(
    [API]='\033[1;35m' [OK]='\033[1;32m' [INFO]='\033[0;34m'
    [DIM]='\033[2m'    [RESET]='\033[0m' [ERROR]='\033[1;31m' [WARN]='\033[95m'
)
SEPARATOR="${COLORS[INFO]}───────────────────────────────────────────────────────────────${COLORS[RESET]}"

# --- Логирование ---
log()       { echo -e "${COLORS[$1]}[$1]${COLORS[RESET]} $2"; }
log_info()  { log INFO "$1"; }
log_ok()    { log OK "$1"; }
log_warn()  { log WARN "$1"; }
log_error() { log ERROR "$1" >&2; }

# --- Проверка инструментов ---
require_tools() {
    for cmd in sqlite3 jq curl; do
        command -v "$cmd" &>/dev/null || { log_error "Требуется '$cmd'"; exit 1; }
    done
}

# --- API вызов ---
gemini_api_call() {
    local request_body="$1" response error
    while true; do
        response=$(curl -sS -X POST \
            -H "Content-Type: application/json" \
            -H "X-goog-api-key: $CURRENT_API_KEY" \
            -d "$request_body" \
            "${BASE_API_URL}${MODEL}:generateContent") || {
                log_error "Сеть недоступна или запрос не удался"
                return 1
            }

        error=$(jq -r '.error.message // empty' <<<"$response")
        if [[ -n "$error" ]]; then
            log_error "Ошибка API: ${COLORS[DIM]}$error${COLORS[RESET]}"
            echo "Действия: [k] сменить ключ, [m] сменить модель, [q] выход."
            read -rp "Ваш выбор (k/m/q): " action
            case "$action" in
                k|K)
                    read -rsp "Введите новый GOOGLE_API_KEY: " new_key
                    echo
                    [[ -n "$new_key" ]] || { log_error "Ключ не введён"; continue; }
                    CURRENT_API_KEY="$new_key"
                    log_ok "Ключ обновлён."
                    ;;
                m|M)
                    echo "Доступные модели:"
                    for i in "${!AVAILABLE_MODELS[@]}"; do
                        echo "  $((i+1))) ${AVAILABLE_MODELS[i]}"
                    done
                    read -rp "Выберите модель (номер): " idx
                    if [[ "$idx" =~ ^[0-9]+$ ]] && (( idx>=1 && idx<=${#AVAILABLE_MODELS[@]} )); then
                        MODEL="${AVAILABLE_MODELS[idx-1]}"
                        log_ok "Выбрана модель: $MODEL"
                    else
                        log_error "Неверный выбор модели."
                    fi
                    ;;
                q|Q)
                    log_warn "Завершение работы по выбору пользователя."
                    exit 0
                    ;;
                *)
                    log_warn "Неизвестный ввод, повторите."
                    ;;
            esac
            continue
        fi
        echo "$response"
        return 0
    done
}

# --- Основная логика ---
main() {
    require_tools
    [[ -n "$CURRENT_API_KEY" ]] || { log_error "GOOGLE_API_KEY не найден"; exit 1; }

    # Проверка/создание колонки
    if ! sqlite3 "$DB_FILE" "PRAGMA table_info('$TABLE_NAME');" | grep -q "|$DST_COLUMN|"; then
        log_warn "Колонка '$DST_COLUMN' отсутствует. Создаю..."
        sqlite3 "$DB_FILE" "ALTER TABLE \"$TABLE_NAME\" ADD COLUMN \"$DST_COLUMN\" TEXT;"
        log_ok "Колонка добавлена."
    fi

    local total remaining
    total=$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM \"$TABLE_NAME\";")
    remaining=$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM \"$TABLE_NAME\" WHERE \"$DST_COLUMN\" IS NULL OR \"$DST_COLUMN\"='';")
    (( remaining == 0 )) && { log_ok "Все $total строк уже переведены."; exit 0; }

    log_info "Всего строк: $total. Осталось перевести: $remaining."

    while (( remaining > 0 )); do
        mapfile -t batch < <(
            sqlite3 -separator '|||' "$DB_FILE" \
            "SELECT \"$PK_COLUMN\", \"$SRC_COLUMN\" FROM \"$TABLE_NAME\" \
             WHERE \"$DST_COLUMN\" IS NULL OR \"$DST_COLUMN\"='' \
             ORDER BY \"$PK_COLUMN\" ASC LIMIT $BATCH_SIZE;"
        )
        (( ${#batch[@]} == 0 )) && break

        local prompt="Translate the following list from $SRC_LANG to $DST_LANG.
Strict rules:
1. Output must be a numbered list ONLY.
2. Each translation must exactly match the input in meaning, no omissions.
3. No commentary, no formatting beyond numbers.
"
        local ids=()
        for i in "${!batch[@]}"; do
            pk="${batch[i]%%|||*}"
            txt="${batch[i]#*|||}"
            ids+=("$pk")
            prompt+="\n$((i+1)). $txt"
        done

        log_info "Отправка ${#ids[@]} строк в $MODEL..."
        local start=$(date +%s.%N)
        local req=$(jq -n --arg text "$prompt" '{"contents":[{"parts":[{"text":$text}]}]}')
        local resp; resp=$(gemini_api_call "$req") || { sleep 5; continue; }
        local elapsed=$(echo "$(date +%s.%N) - $start" | bc)
        log_ok "Ответ за ${elapsed}s"

        local text; text=$(jq -r '.candidates[0].content.parts[0].text // empty' <<<"$resp")
        mapfile -t translations < <(echo -e "$text" | sed 's/^[0-9]\+\.\s*//')

        if (( ${#translations[@]} != ${#ids[@]} )); then
            log_warn "Несоответствие строк (${#translations[@]} != ${#ids[@]})."
            if (( ${#translations[@]} > 1 )); then
                local save_count=$(( ${#translations[@]} - 1 ))
                log_warn "Сохраняю $save_count строк (частично)."
                {
                    echo "BEGIN;"
                    for i in $(seq 0 $((save_count-1))); do
                        safe=$(printf "%s" "${translations[i]}" | sed "s/'/''/g")
                        echo "UPDATE \"$TABLE_NAME\" SET \"$DST_COLUMN\"='$safe' WHERE \"$PK_COLUMN\"=${ids[i]};"
                    done
                    echo "COMMIT;"
                } | sqlite3 "$DB_FILE"
                log_warn "Последние строки ответа:"
                printf '%s\n' "${translations[@]: -3}"
            fi
            sleep 5
            continue
        fi

        {
            echo "BEGIN;"
            for i in "${!ids[@]}"; do
                safe=$(printf "%s" "${translations[i]}" | sed "s/'/''/g")
                echo "UPDATE \"$TABLE_NAME\" SET \"$DST_COLUMN\"='$safe' WHERE \"$PK_COLUMN\"=${ids[i]};"
            done
            echo "COMMIT;"
        } | sqlite3 "$DB_FILE"

        remaining=$((remaining - ${#ids[@]}))
        local done=$((total - remaining))
        local percent=$((done * 100 / total))
        log_ok "Сохранено. Прогресс: $percent% ($done/$total)"
        echo -e "$SEPARATOR"
        sleep 1
    done

    log_ok "Перевод завершён!"
}

main
