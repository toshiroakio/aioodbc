import asyncio
import gc
import os
import sys
from unittest import mock

import pytest
import pyodbc

import aioodbc
from aioodbc.cursor import Cursor


PY_341 = sys.version_info >= (3, 4, 1)


class TestConversion:

    def test_connect(self, loop, conn):
        assert conn.loop is loop
        assert not conn.autocommit
        assert conn.timeout == 0
        assert not conn.closed

    @pytest.mark.run_loop
    def test_basic_cursor(self, conn):
        cursor = yield from conn.cursor()
        sql = 'SELECT 10;'
        yield from cursor.execute(sql)
        (resp, ) = yield from cursor.fetchone()
        assert resp == 10

    @pytest.mark.run_loop
    def test_default_event_loop(self, conn_no_loop):
        loop = asyncio.get_event_loop()

        cur = yield from conn_no_loop.cursor()
        assert isinstance(cur, Cursor)
        yield from cur.execute('SELECT 1;')
        (ret, ) = yield from cur.fetchone()
        assert 1 == ret
        assert conn_no_loop._loop is loop

    @pytest.mark.run_loop
    def test_close_twice(self, conn):
        yield from conn.ensure_closed()
        yield from conn.ensure_closed()
        assert conn.closed

    @pytest.mark.run_loop
    def test_execute(self, conn):
        cur = yield from conn.execute('SELECT 10;')
        (resp, ) = yield from cur.fetchone()
        yield from conn.ensure_closed()
        assert resp == 10
        assert conn.closed

    @pytest.mark.run_loop
    def test_getinfo(self, conn):
        data = yield from conn.getinfo(pyodbc.SQL_CREATE_TABLE)
        assert data == 1793

    @pytest.mark.run_loop
    def test_output_conversion(self, conn):
        def convert(value):
            # `value` will be a string.  We'll simply add an X at the
            # beginning at the end.
            return 'X' + value + 'X'
        yield from conn.add_output_converter(pyodbc.SQL_VARCHAR, convert)
        cur = yield from conn.cursor()

        yield from cur.execute("DROP TABLE t1;")
        yield from cur.execute("CREATE TABLE t1(n INT, v VARCHAR(10))")
        yield from cur.execute("INSERT INTO t1 VALUES (1, '123.45')")
        yield from cur.execute("SELECT v FROM t1")
        (value, ) = yield from cur.fetchone()

        assert value == 'X123.45X'

        # Now clear the conversions and try again.  There should be
        # no Xs this time.
        yield from conn.clear_output_converters()
        yield from cur.execute("SELECT v FROM t1")
        (value, ) = yield from cur.fetchone()
        assert value == '123.45'
        yield from cur.execute("DROP TABLE t1;")

    def test_autocommit(self, loop, connection_maker):
        conn = connection_maker(loop, autocommit=True)
        assert conn.autocommit, True

    @pytest.mark.run_loop
    def test_rollback(self, conn):
        assert not conn.autocommit

        cur = yield from conn.cursor()
        yield from cur.execute("DROP TABLE t1;")
        yield from cur.execute("CREATE TABLE t1(n INT, v VARCHAR(10));")

        yield from conn.commit()

        yield from cur.execute("INSERT INTO t1 VALUES (1, '123.45');")
        yield from cur.execute("SELECT v FROM t1")
        (value, ) = yield from cur.fetchone()
        assert value == '123.45'

        yield from conn.rollback()
        yield from cur.execute("SELECT v FROM t1;")
        value = yield from cur.fetchone()
        assert value is None

        yield from conn.ensure_closed()

    @pytest.mark.skipif(not PY_341, reason=
                       "Python 3.3 doesnt support __del__ calls from GC")
    @pytest.mark.run_loop
    def test___del__(self, loop, recwarn):
        dsn = os.environ.get('DSN', 'Driver=SQLite;Database=sqlite.db')
        conn = yield from aioodbc.connect(dsn, loop=loop)
        exc_handler = mock.Mock()
        loop.set_exception_handler(exc_handler)

        del conn
        gc.collect()

        w = recwarn.pop()
        assert issubclass(w.category, ResourceWarning)

        msg = {'connection': mock.ANY,  # conn was deleted
               'message': 'Unclosed connection'}
        if loop.get_debug():
            msg['source_traceback'] = mock.ANY
        exc_handler.assert_called_with(loop, msg)
