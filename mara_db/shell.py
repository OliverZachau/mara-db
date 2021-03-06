"""
Shell command generation for
- running queries in databases via their comannd line clients
- copying data from, into and between databases
"""

import shlex
from functools import singledispatch

from mara_db import dbs, config
from multimethod import multidispatch


@singledispatch
def query_command(db: object, timezone: str = None, echo_queries: bool = True) -> str:
    """
    Creates a shell command that receives a sql query from stdin and executes it

    Args:
        db: The database in which to run the query (either an alias or a `dbs.DB` object
        timezone: Sets the timezone of the client, if applicable
        echo_queries: Whether the client should print executed queries, if applicable

    Returns:
        A shell command string

    Example:
        >>> print(query_command('mara', 'America/New_York'))
        PGTZ=America/New_York PGOPTIONS=--client-min-messages=warning psql --username=root --host=localhost \
            --echo-all --no-psqlrc --set ON_ERROR_STOP=on mara


        >>> print(query_command(dbs.MysqlDB(host='localhost', database='test')))
        mysql --default-character-set=utf8mb4 --host=localhost test
    """
    raise NotImplementedError(f'Please implement query_command for type "{db.__class__.__name__}"')


@query_command.register(str)
def __(alias: str, timezone: str = None, echo_queries: bool = True):
    return query_command(dbs.db(alias), timezone=timezone, echo_queries=echo_queries)


@query_command.register(dbs.PostgreSQLDB)
def __(db: dbs.PostgreSQLDB, timezone: str = None, echo_queries: bool = True):
    return (f'PGTZ={timezone or config.default_timezone()} '
            + (f'PGPASSWORD={db.password} ' if db.password else '')
            + 'PGOPTIONS=--client-min-messages=warning psql'
            + (f' --username={db.user}' if db.user else '')
            + (f' --host={db.host}' if db.host else '')
            + (f' --port={db.port}' if db.port else '')
            + (' --echo-all' if echo_queries else ' ')
            + ' --no-psqlrc --set ON_ERROR_STOP=on '
            + (db.database or ''))


@query_command.register(dbs.MysqlDB)
def __(db: dbs.MysqlDB, timezone: str = None, echo_queries: bool = True):
    return ((f"MYSQL_PWD='{db.password}' " if db.password else '')
            + 'mysql '
            + (f' --user={db.user}' if db.user else '')
            + (f' --host={db.host}' if db.host else '')
            + (f' --port={db.port}' if db.port else '')
            + (' --ssl' if db.ssl else '')
            + (f' {db.database}' if db.database else ''))


@query_command.register(dbs.SQLServerDB)
def __(db: dbs.SQLServerDB, timezone: str = None, echo_queries: bool = True):
    # sqsh is not able to use '$' directly, it has to be quoted by two backslashes
    # first, undo the quoting in case it has already been applied, then quote
    command = "sed 's/\\\\\\\\$/\$/g;s/\$/\\\\\\\\$/g' | "

    # sqsh does not do anything when a statement is not terminated by a ';', add on to be sure
    command += "(cat && echo ';') \\\n  | "
    command += "(cat && echo ';\n\go') \\\n  | "

    return (command + 'sqsh '
            + (f' -U {db.user}' if db.user else '')
            + (f' -P {db.password}' if db.password else '')
            + (f' -S {db.host}' if db.host else '')
            + (f' -D {db.database}' if db.database else ''))


@query_command.register(dbs.SQLiteDB)
def __(db: dbs.SQLiteDB, timezone: str = None, echo_queries: bool = True):
    # sqlite does not complain if a file does not exist. Therefore check file existence first
    file_name = shlex.quote(str(db.file_name))
    return f'(test -f {file_name} && cat || >&2 echo {file_name} not found) \\\n' \
           + '  | sqlite3 -bail ' + shlex.quote(str(db.file_name))


# -------------------------------


@singledispatch
def copy_to_stdout_command(db: object) -> str:
    """
    Creates a shell command that receives a query from stdin, executes it and writes the output to stdout

    Args:
        db: The database in which to run the query (either an alias or a `dbs.DB` object

    Returns:
        The composed shell command

    Example:
        >>> print(copy_to_stdout_command(dbs.PostgreSQLDB(host='localhost', database='test')))
        PGTZ=Europe/Berlin PGOPTIONS=--client-min-messages=warning psql --host=localhost  --no-psqlrc --set ON_ERROR_STOP=on test --tuples-only --no-align --field-separator='	' \
            | grep -a -v -e '^$'
    """
    raise NotImplementedError(f'Please implement function copy_to_stdout_command for type "{db.__class__.__name__}"')


@copy_to_stdout_command.register(str)
def __(alias: str):
    return copy_to_stdout_command(dbs.db(alias))


@copy_to_stdout_command.register(dbs.PostgreSQLDB)
def __(db: dbs.PostgreSQLDB):
    return query_command(db, echo_queries=False) \
           + " --tuples-only --no-align --field-separator='\t' \\\n" \
           + "  | sed '/^$/d'"  # remove empty lines


@copy_to_stdout_command.register(dbs.MysqlDB)
def __(db: dbs.MysqlDB):
    return query_command(db) + ' --skip-column-names'


@copy_to_stdout_command.register(dbs.SQLServerDB)
def __(db: dbs.SQLServerDB):
    return query_command(db) + " -m csv"


@copy_to_stdout_command.register(dbs.SQLiteDB)
def __(db: dbs.SQLiteDB):
    return query_command(db) + " -noheader -separator '\t' -quote"


# -------------------------------


@singledispatch
def copy_from_stdin_command(db: object, target_table: str,
                            csv_format: bool = False, skip_header: bool = False,
                            delimiter_char: str = None, quote_char: str = None,
                            null_value_string: str = None, timezone: str = None):
    """
    Creates a shell command that receives data from stdin and writes it to a table.

    Options are tailored for the PostgreSQL `COPY FROM STDIN` command, adaptions might be needed for other databases.
    https://www.postgresql.org/docs/current/static/sql-copy.html

    Args:
        db: The database to use (either an alias or a `dbs.DB` object
        target_table: The table in which the data is written
        csv_format: Treat the input as a CSV file (comma separated, double quoted literals)
        skip_header: When true, skip the first line
        delimiter_char: The character that separates columns
        quote_char: The character for quoting strings
        null_value_string: The string that denotes NULL values
        timezone: Sets the timezone of the client, if applicable

    Returns:
        The composed shell command

    Examples:
        >>>> print(copy_from_stdin_command('mara', target_table='foo'))
        PGTZ=Europe/Berlin PGOPTIONS=--client-min-messages=warning psql --username=root --host=localhost --echo-all --no-psqlrc --set ON_ERROR_STOP=on mara \
            --command="COPY foo FROM STDIN WITH CSV"
    """
    raise NotImplementedError(f'Please implement copy_from_stdin_command for type "{db.__class__.__name__}"')


@copy_from_stdin_command.register(str)
def __(alias: str, target_table: str, csv_format: bool = False, skip_header: bool = False,
       delimiter_char: str = None, quote_char: str = None, null_value_string: str = None, timezone: str = None):
    return copy_from_stdin_command(
        dbs.db(alias), target_table=target_table, csv_format=csv_format, skip_header=skip_header,
        delimiter_char=delimiter_char, quote_char=quote_char,
        null_value_string=null_value_string, timezone=timezone)


@copy_from_stdin_command.register(dbs.PostgreSQLDB)
def __(db: dbs.PostgreSQLDB, target_table: str, csv_format: bool = False, skip_header: bool = False,
       delimiter_char: str = None, quote_char: str = None, null_value_string: str = None, timezone: str = None):
    sql = f'COPY {target_table} FROM STDIN WITH'
    if csv_format:
        sql += ' CSV'
    if skip_header:
        sql += ' HEADER'
    if delimiter_char != None:
        sql += f" DELIMITER AS '{delimiter_char}'"
    if null_value_string != None:
        sql += f" NULL AS '{null_value_string}'"
    if quote_char != None:
        sql += f" QUOTE AS '{quote_char}'"

    return f'{query_command(db, timezone)} \\\n      --command="{sql}"'


# -------------------------------


@multidispatch
def copy_command(source_db: object, target_db: object, target_table: str, timezone: str):
    """
    Creates a shell command that
    - receives a sql query from stdin
    - executes the query in `source_db`
    - writes the results of the query to `target_table` in `target_db`

    Args:
        source: The database in which to run the query (either an alias or a `dbs.DB` object
        target_db: The database where to write the query results (alias or db configuration)
        target_table: The table in which to write the query results
        timezone: Sets the timezone of the client, if applicable

    Returns:
        A shell command string

    Examples:
        >>>> print(copy_command(dbs.SQLServerDB(database='source_db'), dbs.PostgreSQLDB(database='target_db'), 'target_table', None))
        sed 's/\\\\$/\$/g;s/\$/\\\\$/g' \
          | sqsh  -D source_db -m csv \
          | PGTZ=Europe/Berlin PGOPTIONS=--client-min-messages=warning psql --echo-all --no-psqlrc --set ON_ERROR_STOP=on target_db \
               --command="COPY target_table FROM STDIN WITH CSV HEADER"
    """
    raise NotImplementedError(
        f'Please implement copy_command for types "{source_db.__class__.__name__}" and "{target_db.__class__.__name__}"')


@copy_command.register(str, str)
def __(source_db_alias: str, target_db_alias: str, target_table: str, timezone: str = None):
    return copy_command(dbs.db(source_db_alias), dbs.db(target_db_alias), target_table, timezone)


@copy_command.register(dbs.DB, str)
def __(source_db: dbs.DB, target_db_alias: str, target_table: str, timezone: str = None):
    return copy_command(source_db, dbs.db(target_db_alias), target_table, timezone)


@copy_command.register(dbs.PostgreSQLDB, dbs.PostgreSQLDB)
def __(source_db: dbs.PostgreSQLDB, target_db: dbs.PostgreSQLDB, target_table: str, timezone: str):
    return (copy_to_stdout_command(source_db) + ' \\\n'
            + '  | ' + copy_from_stdin_command(target_db, target_table=target_table,
                                               null_value_string='', timezone=timezone))


@copy_command.register(dbs.MysqlDB, dbs.PostgreSQLDB)
def __(source_db: dbs.MysqlDB, target_db: dbs.PostgreSQLDB, target_table: str, timezone: str):
    return (copy_to_stdout_command(source_db) + ' \\\n'
            + '  | ' + copy_from_stdin_command(target_db, target_table=target_table,
                                               null_value_string='NULL', timezone=timezone))


@copy_command.register(dbs.SQLServerDB, dbs.PostgreSQLDB)
def __(source_db: dbs.SQLServerDB, target_db: dbs.PostgreSQLDB, target_table: str, timezone: str):
    return (copy_to_stdout_command(source_db) + ' \\\n'
            + '  | ' + copy_from_stdin_command(target_db, target_table=target_table, csv_format=True,
                                               skip_header=True, timezone=timezone))


@copy_command.register(dbs.SQLiteDB, dbs.PostgreSQLDB)
def __(source_db: dbs.SQLiteDB, target_db: dbs.PostgreSQLDB, target_table: str, timezone: str):
    return (copy_to_stdout_command(source_db) + ' \\\n'
            + '  | ' + copy_from_stdin_command(target_db, target_table=target_table, timezone=timezone,
                                               null_value_string='NULL', quote_char="''", csv_format=True))
