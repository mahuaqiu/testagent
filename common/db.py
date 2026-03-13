"""
数据库操作模块 —— 提供测试数据的数据库直接操作能力。

主要用于：测试前数据准备、测试后数据清理、断言数据库状态。

Usage:
    from common.db import DB

    db = DB(host="127.0.0.1", port=3306, user="test", password="test123", database="test_db")
    rows = db.query("SELECT * FROM users WHERE phone = %s", ("13800001111",))
    db.execute("DELETE FROM users WHERE id = %s", (123,))
    db.close()
"""


class DB:
    """数据库操作类（MySQL），封装常用的查询和执行方法。

    注意: 使用前需安装 pymysql: pip install pymysql

    Args:
        host: 数据库主机。
        port: 端口号。
        user: 用户名。
        password: 密码。
        database: 数据库名。
    """

    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        self._config = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "charset": "utf8mb4",
        }
        self._conn = None

    def _get_conn(self):
        """懒加载数据库连接。"""
        if self._conn is None:
            import pymysql
            self._conn = pymysql.connect(**self._config, cursorclass=pymysql.cursors.DictCursor)
        return self._conn

    def query(self, sql: str, params: tuple = None) -> list[dict]:
        """执行查询 SQL，返回字典列表。

        Args:
            sql: SQL 语句，参数用 %s 占位。
            params: 参数元组。

        Returns:
            list[dict]: 查询结果列表。

        Examples:
            >>> rows = db.query("SELECT * FROM users WHERE phone = %s", ("13800001111",))
        """
        conn = self._get_conn()
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def execute(self, sql: str, params: tuple = None) -> int:
        """执行写操作 SQL（INSERT / UPDATE / DELETE），自动提交。

        Args:
            sql: SQL 语句。
            params: 参数元组。

        Returns:
            int: 影响的行数。

        Examples:
            >>> db.execute("DELETE FROM users WHERE id = %s", (123,))
        """
        conn = self._get_conn()
        with conn.cursor() as cursor:
            affected = cursor.execute(sql, params)
            conn.commit()
            return affected

    def close(self):
        """关闭数据库连接。"""
        if self._conn:
            self._conn.close()
            self._conn = None
