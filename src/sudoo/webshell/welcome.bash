# shellcheck shell=bash
# The start file of a web shell's terminal (webshell/welcome.py): what bash reads instead of ~/.bashrc when the
# Terminal opens. It welcomes the person, says where they are, lists the commands that work here, and draws a prompt
# in the colour of the instance's stage.
#
# The panel passes it in the SUDOO_RC variable of the process it starts (docker exec), never as a file in the
# container: every instance gets it, those made before it existed too, without a new file, mount or image.
# What it knows comes from variables the panel sets for the process:
#
#     SUDOO_INSTANCE  SUDOO_STAGE  SUDOO_ODOO_VERSION  SUDOO_EDITION  SUDOO_PROJECT  SUDOO_BRANCH
#     SUDOO_DB  SUDOO_CODE (the repository's folder)  SUDOO_ODOO_CONF  PGHOST PGPORT PGUSER PGDATABASE
#
# Nothing here reaches the network and nothing waits: the welcome is printed at once. NO_COLOR (no-color.org) and
# TERM=dumb turn every colour off.

exec 9<&- # the descriptor this file came through: programs started from here do not need it
unset SUDOO_RC

# What bash reads for an interactive shell without --rcfile: the system's file, then the person's own ~/.bashrc
# (their aliases, history settings and prompt tools keep working; the prompt below is drawn after them).
# shellcheck disable=SC1091
[ -r /etc/bash.bashrc ] && . /etc/bash.bashrc
# shellcheck disable=SC1091
[ -r "$HOME/.bashrc" ] && . "$HOME/.bashrc"

# the instance's name, never the container's: `echo $HOSTNAME` and `hostname` name the instance
export HOSTNAME="${SUDOO_INSTANCE:-$HOSTNAME}"
hostname() {
    if [ $# -eq 0 ]; then printf '%s\n' "$HOSTNAME"; else command hostname "$@"; fi
}

SUDOO_CODE="${SUDOO_CODE:-$PWD}"

# `cd` alone goes back to the code (the repository), where the shell starts
cd() {
    if [ $# -eq 0 ]; then builtin cd -- "$SUDOO_CODE" || return; else builtin cd "$@" || return; fi
}

# ---------------------------------------------------------------- colours

_sudoo_colour=1
if [ -n "${NO_COLOR-}" ] || [ "${TERM-}" = dumb ]; then _sudoo_colour=0; fi
_sudoo_truecolour=0
case "${COLORTERM-}" in truecolor | 24bit) _sudoo_truecolour=1 ;; esac

# the stage's colour, as the panel's stage pills show it: production red, staging amber, development green
case "${SUDOO_STAGE-}" in
    production) _sudoo_stage_sgr='1;31' ;;
    staging) _sudoo_stage_sgr='1;33' ;;
    *) _sudoo_stage_sgr='1;32' ;;
esac

_sudoo_paint() { # _sudoo_paint <SGR> <text>: the text in that colour (or plain)
    if [ "$_sudoo_colour" = 1 ]; then printf '\033[%sm%s\033[0m' "$1" "$2"; else printf '%s' "$2"; fi
}

# ---------------------------------------------------------------- commands

_sudoo_can_odoo() { # Odoo's command and the instance's configuration are within reach (not for root: see below)
    command -v odoo >/dev/null 2>&1 && [ -r "${SUDOO_ODOO_CONF-}" ] && [ -n "${SUDOO_DB-}" ]
}

_sudoo_db_password() {
    if [ -n "${PASSWORD-}" ]; then
        printf '%s' "$PASSWORD"
    elif [ -r "${SUDOO_ODOO_CONF-}" ]; then
        sed -n 's/^db_password[[:space:]]*=[[:space:]]*//p' "$SUDOO_ODOO_CONF" | head -n 1
    fi
}

# psql on the instance's database, signed in already (PGHOST, PGUSER and PGDATABASE come from the panel; the
# password only for psql itself, never exported)
if command -v psql >/dev/null 2>&1; then
    psql() {
        PGPASSWORD="${PGPASSWORD:-$(_sudoo_db_password)}" command psql "$@"
    }
fi

if _sudoo_can_odoo; then
    odoo-shell() {
        command odoo shell -c "$SUDOO_ODOO_CONF" -d "$SUDOO_DB" --no-http "$@"
    }
    odoo-update() { _sudoo_modules update "$@"; }
    odoo-install() { _sudoo_modules install "$@"; }
fi

# odoo-update / odoo-install <module>[,<module>...] (commas or spaces): a second Odoo process on the instance's
# database, without a web server, workers or scheduled actions, that updates or installs the modules and stops.
# The running Odoo picks the change up; in production the instance's name is asked first.
_sudoo_modules() {
    local action=$1 flag modules answer code
    shift
    if [ $# -eq 0 ]; then
        printf 'Usage: odoo-%s <module>[,<module>...]\n' "$action" >&2
        return 2
    fi
    modules=$(printf '%s,' "$@" | tr -s ' ,' ',,')
    modules=${modules#,}
    modules=${modules%,}
    if [[ ! $modules =~ ^[A-Za-z0-9_]+(,[A-Za-z0-9_]+)*$ ]]; then
        printf 'Module names are letters, digits and underscores, separated by commas.\n' >&2
        return 2
    fi
    if [ "${SUDOO_STAGE-}" = production ]; then
        printf '%s\n' "$(_sudoo_paint "$_sudoo_stage_sgr" "This is production:") odoo-$action $modules runs on the live database."
        read -r -p "Type the instance's name ($SUDOO_INSTANCE) to go on: " answer || return 1
        if [ "$answer" != "$SUDOO_INSTANCE" ]; then
            printf 'Nothing was changed.\n'
            return 1
        fi
    fi
    if [ "$action" = install ]; then flag=-i; else flag=-u; fi
    command odoo -c "$SUDOO_ODOO_CONF" -d "$SUDOO_DB" "$flag" "$modules" \
        --stop-after-init --no-http --workers=0 --max-cron-threads=0
    code=$?
    if [ "$code" = 0 ]; then
        printf '%s\n' "$(_sudoo_paint '1;32' "Done:") odoo-$action $modules"
    else
        printf '%s\n' "$(_sudoo_paint '1;31' "Failed (exit code $code):") odoo-$action $modules. Read the lines above." >&2
    fi
    return "$code"
}

# the list of commands, two aligned columns; only those that work in this shell
sudoo-help() {
    local -a rows=()
    local i width=0
    if _sudoo_can_odoo; then
        rows+=("odoo-shell" "Odoo's Python shell on this instance's database")
        rows+=("odoo-update <modules>" "Update modules (comma-separated), then stop")
        rows+=("odoo-install <modules>" "Install modules, then stop")
    fi
    if command -v psql >/dev/null 2>&1; then
        rows+=("psql" "SQL on this instance's database, signed in already")
    fi
    rows+=("cd" "Back to the code (~)")
    rows+=("sudoo-help" "Show this list again")
    for ((i = 0; i < ${#rows[@]}; i += 2)); do
        if [ "${#rows[i]}" -gt "$width" ]; then width=${#rows[i]}; fi
    done
    printf '%s\n' "$(_sudoo_paint 1 "Useful commands")"
    for ((i = 0; i < ${#rows[@]}; i += 2)); do
        printf '  %s%*s  %s\n' "$(_sudoo_paint '38;5;181' "${rows[i]}")" $((width - ${#rows[i]})) '' "${rows[i + 1]}"
    done
    if [ "$(id -u)" = 0 ] && ! _sudoo_can_odoo; then
        printf '  As root, Odoo'"'"'s own commands are not offered: open the Terminal as odoo for them.\n'
    fi
    printf '  Logs, restarts and e-mail are in the panel, in this instance'"'"'s tabs.\n'
}

# ---------------------------------------------------------------- the prompt

# <instance> [<stage>/<version>]:<folder>$ in bold, the instance and stage in the stage's colour; the code's folder
# shows as ~ (the container's own paths never show for it)
_sudoo_where() {
    case "$PWD" in
        "$SUDOO_CODE") _sudoo_folder='~' ;;
        "$SUDOO_CODE"/*) _sudoo_folder="~/${PWD#"$SUDOO_CODE"/}" ;;
        *) _sudoo_folder=$PWD ;;
    esac
}
case ";${PROMPT_COMMAND-};" in
    *";_sudoo_where;"*) ;;
    *) PROMPT_COMMAND="_sudoo_where${PROMPT_COMMAND:+;$PROMPT_COMMAND}" ;;
esac
_sudoo_where
_sudoo_label="${SUDOO_INSTANCE:-odoo} [${SUDOO_STAGE:-development}${SUDOO_ODOO_VERSION:+/$SUDOO_ODOO_VERSION}]"
if [ "$_sudoo_colour" = 1 ]; then
    PS1='\[\033['"$_sudoo_stage_sgr"'m\]${_sudoo_label}\[\033[0;1m\]:${_sudoo_folder}\$\[\033[0m\] '
else
    PS1='${_sudoo_label}:${_sudoo_folder}\$ '
fi

# ---------------------------------------------------------------- the welcome

# @BANNER@

_sudoo_columns() {
    local size
    size=$(stty size 2>/dev/null) && size=${size#* } || size=""
    # unknown, or 0 while the browser has not sent its size yet: assume a normal terminal
    case "$size" in '' | 0 | *[!0-9]*) size=${COLUMNS:-80} ;; esac
    case "$size" in '' | 0 | *[!0-9]*) size=80 ;; esac
    printf '%s' "$size"
}

_sudoo_welcome() {
    local line details=""
    if [ "$(_sudoo_columns)" -ge "$_sudoo_banner_width" ]; then
        if [ "$_sudoo_colour" = 0 ]; then
            for line in "${_sudoo_banner_plain[@]}"; do printf '%s\n' "$line"; done
        elif [ "$_sudoo_truecolour" = 1 ]; then
            for line in "${_sudoo_banner_24[@]}"; do printf '%s\n' "$line"; done
        else
            for line in "${_sudoo_banner_256[@]}"; do printf '%s\n' "$line"; done
        fi
        printf '\n'
    else
        # too narrow for the banner: the name on one line, so the shell always says where it is
        printf '%s\n\n' "$(_sudoo_paint '1;38;5;96' 'SUDOO.SH')"
    fi
    printf 'You are connected to the %s instance %s\n' \
        "$(_sudoo_paint "$_sudoo_stage_sgr" "${SUDOO_STAGE:-development}")" \
        "$(_sudoo_paint "$_sudoo_stage_sgr" "${SUDOO_INSTANCE:-odoo}")"
    if [ -n "${SUDOO_ODOO_VERSION-}" ]; then
        details="running Odoo $SUDOO_ODOO_VERSION${SUDOO_EDITION:+ ($SUDOO_EDITION)}"
    fi
    if [ -n "${SUDOO_PROJECT-}" ]; then details="${details:+$details · }project $SUDOO_PROJECT"; fi
    if [ -n "${SUDOO_BRANCH-}" ]; then details="${details:+$details · }branch $SUDOO_BRANCH"; fi
    if [ -n "$details" ]; then printf '%s\n' "$details"; fi
    case "${SUDOO_STAGE-}" in
        production) printf '%s\n' "$(_sudoo_paint '1;31' "This is production: changes affect live users.")" ;;
        staging | development | '') printf 'E-mail sent from here goes to the mail catcher, not to real people.\n' ;;
    esac
    printf '\n'
    sudoo-help
    printf '\n'
}

case $- in
    *i*) _sudoo_welcome ;;
esac
