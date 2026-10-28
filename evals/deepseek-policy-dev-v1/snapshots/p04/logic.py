from ctypes import c_long


def area(rows, columns):
    return c_long(rows * columns).value
