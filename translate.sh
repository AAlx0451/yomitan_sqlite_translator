#!/bin/bash
set -euo pipefail
IFS=$'\n\t'

# ==============================================================================
# =            Gemini SQLite Batch Translator (v4.1, partial save)             =
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

AVAILABLE_MODELS=("gemini-2.5-flash" "gemini-2.5-flash-lite" "gemini-2.5-pro" "gemini-2.0-flash" "gemini-1.5-flash")

# --- Цвета ---
declare -A COLORS=(
    [API]='\033[1;35m' [OK]='\033[1;32m' [INFO]='\033[0;34m'
    [DIM]='\033[2m'    [RESET]='\033[0m' [ERROR]='\033[1;31m' [WARN]='\033[95m'
)
SEPARATOR="${COLORS[INFO]}──────────────────────────────────────────────────────────────────${COLORS[RESET]}"

# --- Логирование ---
log()       { echo -e "${COLORS[$1]}[$1]${COLORS[RESET]} $2"; }
log_info()  { log INFO "$1"; }
log_ok()    { log OK "$1"; }
log_warn()  { log WARN "$1"; }
log_error() { log ERROR "$1" >&2; }

require_tools() {
    for cmd in sqlite3 jq curl; do
        command -v "$cmd" &>/dev/null || { log_error "Требуется '$cmd'"; exit 1; }
    done
}

# --- API вызов с возможностью смены модели/ключа ---
gemini_api_call() {
    local request_body="$1" response error
    while true; do
        response=$(curl -s -X POST \
            -H "Content-Type: application/json" \
            -H "X-goog-api-key: $CURRENT_API_KEY" \
            -d "$request_body" \
            "${BASE_API_URL}${MODEL}:generateContent")

        error=$(jq -r '.error.message // empty' <<<"$response")
        if [[ -n "$error" ]]; then
            log_error "Ошибка API: ${COLORS[DIM]}$error${COLORS[RESET]}"
            log_warn "Действия: [k] сменить ключ, [m] сменить модель, [q] выход."
            read -rp "Выберите действие (k/m/q): " action
            case "$action" in
                k|K)
                    read -rp "Введите новый GOOGLE_API_KEY: " new_key
                    [[ -n "$new_key" ]] || { log_error "Ключ не введён"; return 1; }
                    CURRENT_API_KEY="$new_key"
                    log_ok "Ключ обновлён. Повторяем запрос..."
                    continue
                    ;;
                m|M)
                    echo -e "Доступные модели:"
                    for i in "${!AVAILABLE_MODELS[@]}"; do
                        echo "  $((i+1))) ${AVAILABLE_MODELS[i]}"
                    done
                    read -rp "Выберите модель (номер): " idx
                    if [[ "$idx" =~ ^[0-9]+$ ]] && (( idx>=1 && idx<=${#AVAILABLE_MODELS[@]} )); then
                        MODEL="${AVAILABLE_MODELS[idx-1]}"
                        log_ok "Модель переключена на $MODEL. Повторяем запрос..."
                        continue
                    else
                        log_error "Неверный выбор. Прерывание."
                        return 1
                    fi
                    ;;
                q|Q)
                    log_error "Прерывание по выбору пользователя."
                    return 1
                    ;;
                *)
                    log_error "Неизвестный ввод. Прерывание."
                    return 1
                    ;;
            esac
        fi
        echo "$response"; return 0
    done
}

# --- Основная логика ---
main() {
    require_tools
    [[ -n "$CURRENT_API_KEY" ]] || { log_error "Не найден GOOGLE_API_KEY"; exit 1; }

    # Проверка колонки для перевода
    if ! sqlite3 "$DB_FILE" "PRAGMA table_info('$TABLE_NAME');" | grep -q "|$DST_COLUMN|"; then
        log_warn "Колонка '$DST_COLUMN' не найдена. Добавляю..."
        sqlite3 "$DB_FILE" "ALTER TABLE \"$TABLE_NAME\" ADD COLUMN \"$DST_COLUMN\" TEXT;" \
            && log_ok "Колонка добавлена"
    fi

    local total remaining
    total=$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM \"$TABLE_NAME\";")
    remaining=$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM \"$TABLE_NAME\" WHERE \"$DST_COLUMN\" IS NULL OR \"$DST_COLUMN\" = '';")
    [[ "$remaining" -eq 0 ]] && { log_ok "Все $total строк уже переведены"; exit 0; }
    log_info "Всего строк: $total. Осталось перевести: $remaining."

    local processed=0
    while true; do
        mapfile -t batch < <(
            sqlite3 -separator '|||' "$DB_FILE" \
            "SELECT \"$PK_COLUMN\", \"$SRC_COLUMN\" FROM \"$TABLE_NAME\" WHERE \"$DST_COLUMN\" IS NULL OR \"$DST_COLUMN\"='' ORDER BY \"$PK_COLUMN\" ASC LIMIT $BATCH_SIZE;"
        )
        (( ${#batch[@]} == 0 )) && break

        local prompt="Translate the following list from $SRC_LANG to $DST_LANG.
Strict rules:
1. Output only a numbered list.
2. Each translation must exactly match the corresponding input line in meaning.
3. No commentary or formatting.
"
        local ids=()
        for i in "${!batch[@]}"; do
            pk="${batch[i]%%|||*}"
            txt="${batch[i]#*|||}"
            ids+=("$pk")
            prompt+="\n$((i+1)). $txt"
        done

        log_info "Отправка пакета из ${#ids[@]} строк в $MODEL..."
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
                local count=$(( ${#translations[@]} - 1 ))
                log_warn "Сохраняю частично: $count строк."

                {
                    echo "BEGIN;"
                    for i in $(seq 0 $((count-1))); do
                        safe=$(printf "%s" "${translations[i]}" | sed "s/'/''/g")
                        echo "UPDATE \"$TABLE_NAME\" SET \"$DST_COLUMN\"='$safe' WHERE \"$PK_COLUMN\"=${ids[i]};"
                    done
                    echo "COMMIT;"
                } | sqlite3 "$DB_FILE"

                local tail_count=3
                log_warn "Последние $tail_count строки из ответа:"
                for j in $(seq $((count-tail_count)) $((count-1))); do
                    printf "  %d. %s\n" "$((j+1))" "${translations[j]}"
                done
            else
                log_warn "Переводов слишком мало, пакет пропущен."
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

        processed=$((processed + ${#ids[@]}))
        local done=$((total - remaining + processed))
        local percent=$((done * 100 / total))
        log_ok "Сохранено. Прогресс: $percent% ($done/$total)"
        echo -e "$SEPARATOR"
        sleep 1
    done

    log_ok "Готово!"
}

main
