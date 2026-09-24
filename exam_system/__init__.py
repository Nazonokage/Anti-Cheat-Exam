# PyMySQL is used as a MySQLdb drop-in so Windows/XAMPP setups do not need
# a compiled mysqlclient wheel. Harmless when the project is on SQLite.
try:
    import pymysql

    pymysql.install_as_MySQLdb()
except ImportError:
    pass
