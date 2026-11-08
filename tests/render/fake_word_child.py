"""Подмена дочернего процесса Word для проверки тайм-аута (импортируется дочерним процессом spawn)."""

import time


def hanging_child(docx, pdf_out, author, queue):
    queue.put(("pids", [424242]))  # как будто Word запустился с этим PID
    time.sleep(60)  # и завис на окне-предупреждении


def silent_child(docx, pdf_out, author, queue):
    time.sleep(60)  # Word завис при запуске (окно активации), о себе ничего не сообщил
