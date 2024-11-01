#!/bin/bash

# Define color codes using tput
COLOR_TIMESTAMP=$(tput setaf 8) # Gray
COLOR_INFO=$(tput setaf 2)      # Green
COLOR_DEBUG=$(tput setaf 4)     # Blue
COLOR_WARN=$(tput setaf 3)      # Yellow
COLOR_ERROR=$(tput setaf 1)     # Red
RESET=$(tput sgr0)              # Reset
BOLD=$(tput bold)               # Bold

get_time() {
    date +%s.%N
}

# Logging function
log() {
    [[ -z $START ]] && {
        echo "must run setup_log()"
        exit 1
    }

    local timestamp
    local level
    local message
    timestamp="$(printf "%.3fs" "$(bc <<< "$(get_time) - $START")")"
    level="$1"
    message="${*:2}"

    # Output with space alignment
    case "$level" in
        INFO)
            printf "%s%19s %16s %s\n" "${BOLD}${LOG_PREFIX:-}${RESET}" "${COLOR_TIMESTAMP}${timestamp}${RESET}" "${COLOR_INFO}${level}${RESET}" "$message"
            ;;
        DEBUG)
            [ "${LOG_LEVEL:-INFO}" = "DEBUG" ] && printf "%s%19s %16s %s\n" "${BOLD}${LOG_PREFIX:-}${RESET}" "${COLOR_TIMESTAMP}${timestamp}${RESET}" "${COLOR_DEBUG}${level}${RESET}" "$message"
            ;;
        WARN)
            printf "%s%19s %16s %s\n" "${BOLD}${LOG_PREFIX:-}${RESET}" "${COLOR_TIMESTAMP}${timestamp}${RESET}" "${COLOR_WARN}${level}${RESET}" "$message"
            ;;
        ERROR)
            printf "%s%19s %16s %s\n" "${BOLD}${LOG_PREFIX:-}${RESET}" "${COLOR_TIMESTAMP}${timestamp}${RESET}" "${COLOR_ERROR}${level}${RESET}" "$message"
            ;;
        *)
            printf "%s%19s %16s %s\n" "${BOLD}${LOG_PREFIX:-}${RESET}" "${COLOR_TIMESTAMP}${timestamp}${RESET}" "$level" "$message"
            ;;
    esac
}

setup_log() {
    START=$(get_time)
    export START
    export LOG_PREFIX="${LOG_PREFIX:-}"
    export LOG_LEVEL="${LOG_LEVEL:-INFO}"
}
