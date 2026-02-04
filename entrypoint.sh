#!/bin/sh
set -eu

# Проверяем конфиг перед стартом
nginx -t

exec "$@"
