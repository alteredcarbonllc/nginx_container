# Rootless builder: блокировки и очистка

Общий host-tool хранится в nginx_container/ops/ci-builder и устанавливается
в /usr/local/bin/ac-ci-builder-store. Его используют NGINX и PHP пайплайны.
Rootful production-cleanup не меняется. ac_ci_test уже выведен из эксплуатации.

## Политика

- UID строго 1001, rootless Podman, graphroot строго
  /var/lib/ac-ci-builder/.local/share/containers/storage.
- Только localhost/nginx-container:<40 hex> и localhost/php-carbonblog:<40 hex>.
- Последние 3 различных image ID каждого проекта сохраняются по Created.
- Все образы возрастом <=7 суток сохраняются. Возраст считается от Created,
  а не времени последнего использования или присвоения тега.
- Сохраняются образы любых существующих контейнеров, в том числе остановленных.
- Образы с дополнительными неизвестными тегами, без тегов, базовые образы
  и известные родители других образов сохраняются.
- Перед каждым удалением повторяется инвентаризация.
- Только rmi --no-prune без force, без удаления контейнеров и volumes.
  При отказе Podman из-за использования/дочерних образов кандидат пропускается.

Кеш и промежуточные слои остаются. Это ограничивает накопление именованных
результатов сборок, но не устанавливает жёсткий лимит размера всего хранилища.

## Блокировки

Пайплайн запускает build-test-import под общей shared-блокировкой store.lock
и exclusive-блокировкой своего проекта. Разные проекты собираются параллельно,
один проект последовательно. Дескрипторы наследуются командой через exec.
Блокировка покрывает импорт rootless-образа в rootful-хранилище.
Production-deploy выполняется после освобождения builder-блокировки.

Очистка берёт exclusive store.lock без ожидания. При занятости — SKIPPED, код 0.
Это кооперативная защита: ручные build/tag/rmi/export тоже нужно запускать через
ac-ci-builder-store run nginx|php -- COMMAND. Она не блокирует произвольные
команды Podman, которые не используют wrapper.
Файлы lock нельзя удалять или заменять во время работы.

## Порядок внедрения

1. На ноутбуке проверить чистоту обоих репозиториев, распаковать архив
   в /home/kyiv/git (архив содержит каталоги nginx_container и
   php_fpm_carbonblog_org). Посмотреть git diff.
2. ДО push отправить архив через scp -P 1444 на user1@openmailserver.net:/tmp/.
   На VPS распаковать ops/ci-builder во временный каталог и выполнить install.sh
   от root. Это устанавливает только host-tool и lock-файлы; ничего не удаляет.
3. Commit/push обоих репозиториев с ноутбука. Дождаться успешных двух пайплайнов
   и завершения всех старых заданий, запущенных со старыми pipeline-файлами.
   cleanup-builder пока пишет SKIPPED: activation flag absent.
4. На VPS выполнить dry-run от builder из доступного каталога (/tmp):

```sh
(
  cd /tmp || exit 1
  sudo -u ac-ci-builder /usr/local/bin/ac-ci-builder-store cleanup
)
```

5. Только после перевода ОБОИХ пайплайнов и завершения старых заданий включить:

```sh
sudo install -d -o root -g root -m 0755 /etc/ac
sudo install -o root -g root -m 0644 /dev/null /etc/ac/ci-builder-cleanup-enabled
(
  cd /tmp || exit 1
  sudo -u ac-ci-builder /usr/local/bin/ac-ci-builder-store cleanup --apply
)
```

Далее очистка выполняется после успешного deploy каждого из двух пайплайнов.
Если сборки идут одновременно, она может пропустить запуск; следующий успешный
деплой повторит попытку. Отдельного cron нет. Ошибка очистки выводит warning,
но не превращает успешный production-deploy в неуспешный.

Установка повторно не сбрасывает флаг активации и не заменяет lock-файлы.
Исходники на VPS затем можно синхронизировать обычным git pull --ff-only.

## Отключение и откат

```sh
sudo rm -f /etc/ac/ci-builder-cleanup-enabled
```

Это запрещает новые применения cleanup. Уже запущенную очистку нужно дождаться.
До возврата любого старого pipeline без wrapper сначала отключить очистку и
дождаться завершения её текущего запуска. Общие host-tool и lock-файлы оставить,
пока хоть один pipeline их использует. При восстановлении pipeline через Git
учитывать, что push запускает сборку и production-deploy как обычно.

Удалённые rootless-образы не восстанавливаются из этой утилиты: их можно собрать
заново из Git. Production-образы и production rollback-контейнеры — другое
хранилище, они не затрагиваются.

## Проверки

```sh
python3 -m unittest discover -s ops/ci-builder/tests -v
```

Проверены 14 тестов политики и реальное взаимодействие flock между процессами,
включая наследование блокировки дочерним процессом. Проверен синтаксис shell
и структура обоих YAML. Реальный Podman/CI доступен только на VPS: первый
dry-run и оба CI-прогона являются проверкой интеграции.

PostgreSQL: project key `postgresql`, image `localhost/postgresql-ac`, lock `postgresql.lock`.
