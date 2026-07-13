import os
import posixpath
from datetime import datetime, timedelta, timezone
import pandas as pd
import paramiko
from sqlalchemy import text, create_engine
from dotenv import load_dotenv
import stat

load_dotenv()

def ensure_remote_dir(sftp, remote_dir):
    """
    Crea todo el árbol del directorio remoto (mkdir -p).
    Usa chdir/stat para verificar existencia y distinguir archivos/directorios.
    """
    remote_dir = posixpath.normpath(remote_dir)
    if remote_dir in ("", "/"):
        return

    # Construir incrementalmente: /a, /a/b, /a/b/c ...
    parts = [p for p in remote_dir.split("/") if p]  # evita '' por dobles barras
    path = "/" if remote_dir.startswith("/") else ""
    for part in parts:
        path = posixpath.join(path, part) if path else part
        try:
            attrs = sftp.stat(path)
            if not stat.S_ISDIR(attrs.st_mode):
                raise NotADirectoryError(f"Existe pero no es directorio: {path}")
        except FileNotFoundError:
            sftp.mkdir(path)  # crea nivel actual
        # (Opcional) setear permisos:
        # sftp.chmod(path, 0o755)

def connect_sftp(host, port, username, password=None, key_filename=None, timeout=20):
    """
    Abre una conexión SFTP. Usa password o key_filename (llave privada).
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # Acepta hostkey (ajusta en producción)
    client.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        key_filename=key_filename,
        look_for_keys=False if password else True,
        allow_agent=False,
        timeout=timeout,
    )
    return client, client.open_sftp()

def upload_file_creating_dirs(host, port, username, local_path, remote_path,
                              password=None, key_filename=None):
    """
    Sube local_path a remote_path creando directorios intermedios.
    remote_path DEBE incluir el nombre del archivo final.
    """
    print(host, port, username, local_path, remote_path)
    client, sftp = connect_sftp(
        host=host,
        port=port,
        username=username,
        password=password,
        key_filename=key_filename,
    )
    try:
        remote_dir = posixpath.dirname(remote_path)
        if remote_dir:
            ensure_remote_dir(sftp, remote_dir)
        sftp.put(local_path, remote_path)
        # (Opcional) permisos del archivo subido:
        # sftp.chmod(remote_path, 0o644)
        print(f"Subido OK: {local_path} -> {remote_path}")
    finally:
        try:
            sftp.close()
        finally:
            client.close()

def get_grenery_data(month, year):
    # Parámetros de conexión
    usuario = os.getenv("DB_USER")
    password = os.getenv("DB_PASS")
    host = os.getenv("DB_HOST")
    puerto = os.getenv("DB_PORT")
    base_datos = os.getenv("DB_NAME")
    month_formated = month if month > 9 else f"0{month}"


    ahora = datetime.now(timezone.utc) - timedelta(minutes=0)
    hace_5 = ahora - timedelta(minutes=0)
    ahora = ahora.strftime("%Y-%m-%d %H:%M:59")
    hace_5 = hace_5.strftime("%Y-%m-%d %H:%M:00")
    # Crear la conexión con SQLAlchemy
    engine = create_engine(f"postgresql+psycopg2://{usuario}:{password}@{host}:{puerto}/{base_datos}")
    # Consulta SQL para traer las columnas necesarias


    print(hace_5, ahora)

    query = text(f"""select dv.u_time utc_date, dd.name as definition, dv.value
        from data_values_4sec_{month}_{year} as dv
         inner join data_definitions as dd on dd.osi_key = dv.osi_key
         inner join stations as s on s.station_id = dd.station_id
         inner join data_types as dt on dt.data_type = dv.data_type
where
dv.osi_key in (
'03icc033',
'03icc034',
'03icc035',
'03icc036',
'03icc037',
'03icc038',
'03icc039',
'03icc03a',
'03icc03b',
'03icc03c',
'03icc03d',
'03icc03e',
'03icc03f',
'03icc040',
'03icc041'
)
 and  dv.u_time BETWEEN
          '2026-07-10 02:00:00' AND '2026-07-09 02:59:59'
order by dv.time desc, definition asc;""")
    print(query)
    # Leer el resultado en un DataFrame
    # --- Leer a DataFrame ---
    #try:
    with engine.connect() as conn:
        df = pd.read_sql_query(query, conn)
    # Normaliza hora_utc a datetime con tz UTC (opcional)
    if "utc_date" in df.columns:
        df["utc_date"] = pd.to_datetime(df["utc_date"], utc=True, errors="coerce")
        df["minute5"] = (df["utc_date"].dt.minute // 1) * 1
        df["day"] = df["utc_date"].dt.day
        df["hour"] = df["utc_date"].dt.hour
        df["utc_date"] = df["utc_date"].dt.strftime("%Y-%m-%d %H:%M:%S")

    engine.dispose()

    # Supongamos que tu dataframe ya está cargado en df
    # y la columna hora_utc es un datetime


    groups = df.groupby(["day","hour","minute5"])
    for (day, hour,min), g in groups:
        print(min)
        month = datetime.now().month
        day = int(day)
        dir_date = g[["utc_date"]].iloc[0]["utc_date"][0:10].split("-")
        dir = f"{int(dir_date[0])}{int(dir_date[1])}{int(dir_date[2])}"
        dirpath = f"prices/{dir}{hour}/"
        print(dirpath)
        os.makedirs(dirpath, exist_ok=True)
        d = g[["utc_date", "definition", "value"]]
        d.to_parquet(f"{dirpath}/data_{min:02d}.parquet", index=False)
        destiny = f"prices/{dir}{hour}/"
        print(f"{destiny}")
        upload_file_creating_dirs(
            host=os.getenv("ERCOT_HOST_SFTP"),
            port=os.getenv("ERCOT_PORT_SFTP"),
            username=os.getenv("ERCOT_USER_SFTP"),
            local_path=f"{dirpath}data_{min:02d}.parquet",
            remote_path=f"{destiny}data_{min:02d}.parquet",
            password=os.getenv("ERCOT_PASSWORD_SFTP"),
        )
    print("-------------------------------------------------------Terminé--------------------------------------------")

    #except Exception as e:
        # Mensaje útil si la tabla no existe u otro error SQL
    #    print(f"Fallo al leer 'data_values_5min_{month}_{year}': {e}")
        #raise RuntimeError(f"Fallo al leer 'data_values_5min_{month_formated}_{year}': {e}") from e

current_year = (datetime.now(timezone.utc) - timedelta(hours=6)).year
current_month = (datetime.now(timezone.utc) - timedelta(hours=6)).month
get_grenery_data(current_month,current_year)