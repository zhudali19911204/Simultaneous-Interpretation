from simultaneous_interpreter.app import InterpreterApp
from simultaneous_interpreter.screen_capture import enable_dpi_awareness


if __name__ == "__main__":
    enable_dpi_awareness()
    InterpreterApp().run()
