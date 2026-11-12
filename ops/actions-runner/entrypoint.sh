#!/bin/bash
set -euo pipefail
set +x
umask 077

if [[ ! -f .runner ]]; then
    if ! IFS= read -r -s registration_token || [[ -z "$registration_token" ]]; then
        printf '%s\n' 'A nonempty registration token is required on stdin.' >&2
        exit 64
    fi

    ./config.sh \
        --url https://github.com/Edulynch/OpenCode-EvoSpec \
        --token "$registration_token" \
        --name evospec-linux-docker \
        --labels evospec-linux-docker \
        --work _work \
        --unattended \
        --disableupdate
fi

unset registration_token
exec ./run.sh
