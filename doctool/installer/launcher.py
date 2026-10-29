"""Точка входа установленной программы (сборка PyInstaller, 0.7.5).

- Без аргументов (ярлык, двойной щелчок) - веб-интерфейс: `PySimpleManager web`. Окно консоли остаётся открытым,
  пока программа работает; закрыли окно - программа остановилась. Если программа уже запущена, открывается её
  страница в браузере. При ошибке окно не закрывается сразу - чтобы можно было прочитать сообщение.
- С аргументами - обычная командная строка: `PySimpleManager check -a заявление.pdf`, `validate`, `whois` …

DOCTOOL_NO_BROWSER=1 - не открывать браузер (для проверки сборки, installer/smoke_test.py).
"""
from __future__ import annotations

import os
import sys
import traceback


def _restore_library_path() -> None:
    """Linux: сборка PyInstaller подставляет свои библиотеки через LD_LIBRARY_PATH. Дочерним программам
    (tesseract, браузер) нужны системные - возвращаем прежнее значение (на текущий процесс это не влияет)."""
    if not sys.platform.startswith("linux") or not getattr(sys, "frozen", False):
        return
    orig = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig is not None:
        os.environ["LD_LIBRARY_PATH"] = orig
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)


def _pause() -> None:
    try:
        input("\nНажмите Enter, чтобы закрыть окно…")
    except (EOFError, KeyboardInterrupt, RuntimeError):
        pass


def run() -> int:
    _restore_library_path()
    from doctool.__main__ import main

    argv = sys.argv[1:]
    if argv:                                   # командная строка - как python -m doctool
        main(argv)
        return 0
    argv = ["web"] + (["--no-browser"] if os.environ.get("DOCTOOL_NO_BROWSER") else [])
    try:
        main(argv)
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        if e.code not in (None, 0):
            print(e.code if isinstance(e.code, str) else f"Программа завершилась с кодом {e.code}", file=sys.stderr)
            _pause()
            return e.code if isinstance(e.code, int) else 1
    except Exception:                          # noqa: BLE001 - показать ошибку, а не закрыть окно молча
        traceback.print_exc()
        _pause()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
