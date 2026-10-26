from __future__ import annotations
import asyncio, base64, hashlib, html, json, os, re, shutil, socket, sqlite3, subprocess, sys, tempfile, time, zipfile
from datetime import datetime
import urllib.error, urllib.request
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Signal, Qt, QTimer, QUrl, QEvent
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap, QTextCursor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import *
from telethon import TelegramClient
from telethon.network.connection.tcpmtproxy import ConnectionTcpMTProxyRandomizedIntermediate
from urllib.parse import quote, unquote, urlsplit

ROOT=Path(__file__).resolve().parent; DATA=ROOT/'data'; SESSIONS=DATA/'sessions'; AVATARS=DATA/'avatars'; DB=DATA/'app.db'; SETTINGS=DATA/'settings.json'; LOG=DATA/'import.log'
TGWS_PROXY_BIN=Path.home()/'OneDrive'/'Рабочий стол'/'zapret-discord-youtube-main'/'bin'
TGWS_PROXY_EXE=TGWS_PROXY_BIN/'TgWsProxy_windows.exe'
TGWS_PROXY_BUNDLED_CONFIG=TGWS_PROXY_BIN/'tg-ws-proxy-config.json'
TGWS_PROXY_CONFIG=Path(os.environ.get('APPDATA',Path.home()/'AppData'/'Roaming'))/'TgWsProxy'/'config.json'
UPDATE_REPOSITORY='sonyhell666/tg-acc-manager'
UPDATE_BRANCH='main'
UPDATE_CHECK_INTERVAL_MS=6*60*60*1000
DATA.mkdir(parents=True,exist_ok=True); SESSIONS.mkdir(parents=True,exist_ok=True)
_LOCAL_PROXY_PROCESS=None

def sanitize_log_text(message):
    text=str(message)
    secrets={os.environ.get('TG_API_HASH',''),os.environ.get('TG_PROXY','')}
    try:
        settings=json.loads(SETTINGS.read_text(encoding='utf-8')) if SETTINGS.exists() else {}
        secrets.update((str(settings.get(key,'')) for key in ('api_hash','proxy')))
    except (OSError,json.JSONDecodeError,TypeError):
        pass
    for secret in secrets:
        if len(secret)>=6:
            text=text.replace(secret,'[скрыто]')
    text=re.sub(r'(?i)(socks5h?|https?)://([^:/@\s]+):([^@\s]+)@',r'\1://[скрыто]@',text)
    home=str(Path.home())
    if home:
        text=re.sub(re.escape(home), '[папка пользователя]', text, flags=re.IGNORECASE)
    return text

def write_import_log(message):
    text=sanitize_log_text(message)
    line=f'[{datetime.now().astimezone().isoformat(timespec="seconds")}] {text}\n'
    try:
        if LOG.exists() and LOG.stat().st_size>=2*1024*1024:
            backup=LOG.with_suffix(LOG.suffix+'.1')
            backup.unlink(missing_ok=True)
            LOG.replace(backup)
        with LOG.open('a',encoding='utf-8') as log_file:
            log_file.write(line)
    except OSError as error:
        print(f'Не удалось записать журнал приложения: {error}',file=sys.stderr)

def database():
    c=sqlite3.connect(DB)
    c.execute('CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY,name TEXT UNIQUE,session TEXT NOT NULL,telegram_id INTEGER,first_name TEXT NOT NULL DEFAULT \'\',last_name TEXT NOT NULL DEFAULT \'\',username TEXT NOT NULL DEFAULT \'\',avatar TEXT NOT NULL DEFAULT \'\')')
    account_columns={row[1] for row in c.execute('PRAGMA table_info(accounts)')}
    if 'account_status' not in account_columns: c.execute('ALTER TABLE accounts ADD COLUMN account_status TEXT NOT NULL DEFAULT \'\'')
    columns={row[1] for row in c.execute('PRAGMA table_info(accounts)')}
    for column in ('telegram_id INTEGER','first_name TEXT NOT NULL DEFAULT \'\'','last_name TEXT NOT NULL DEFAULT \'\'','username TEXT NOT NULL DEFAULT \'\'','avatar TEXT NOT NULL DEFAULT \'\'','about TEXT NOT NULL DEFAULT \'\''):
        name=column.split()[0]
        if name not in columns: c.execute(f'ALTER TABLE accounts ADD COLUMN {column}')
    c.execute('CREATE TABLE IF NOT EXISTS chats(id INTEGER PRIMARY KEY,title TEXT,peer TEXT UNIQUE NOT NULL,join_peer TEXT NOT NULL DEFAULT \'\',assigned_session TEXT NOT NULL DEFAULT \'\',is_pinned INTEGER NOT NULL DEFAULT 0)')
    chat_columns={row[1] for row in c.execute('PRAGMA table_info(chats)')}
    if 'join_peer' not in chat_columns: c.execute('ALTER TABLE chats ADD COLUMN join_peer TEXT NOT NULL DEFAULT \'\'')
    if 'assigned_session' not in chat_columns: c.execute('ALTER TABLE chats ADD COLUMN assigned_session TEXT NOT NULL DEFAULT \'\'')
    if 'chat_type' not in chat_columns: c.execute('ALTER TABLE chats ADD COLUMN chat_type TEXT NOT NULL DEFAULT \'\'')
    if 'is_pinned' not in chat_columns: c.execute('ALTER TABLE chats ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0')
    c.commit(); return c

def telegram_mark_icon(size=24):
    pixmap=QPixmap(size,size); pixmap.fill(Qt.transparent)
    painter=QPainter(pixmap); painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor('#2AABEE')); painter.setPen(Qt.NoPen)
    painter.drawEllipse(1,1,size-2,size-2)
    painter.scale(size/24,size/24)
    plane=QPainterPath(); plane.moveTo(4,11); plane.lineTo(20,4); plane.cubicTo(20.8,3.7,21.4,4.1,21.1,5.2); plane.lineTo(18.2,19.2); plane.cubicTo(18,20,17.5,20.2,16.8,19.7); plane.lineTo(12.6,16.5); plane.lineTo(10.3,18.7); plane.cubicTo(9.8,19.1,9.4,18.9,9.4,18.3); plane.lineTo(9.7,13.9); plane.closeSubpath()
    painter.setBrush(QColor('white')); painter.drawPath(plane)
    fold=QPainterPath(); fold.moveTo(9.7,13.9); fold.lineTo(17.7,7.4); fold.cubicTo(18.1,7.1,18.4,7.4,18,7.8); fold.lineTo(12.6,16.5)
    painter.setBrush(Qt.NoBrush); painter.setPen(QColor('#2AABEE')); painter.drawPath(fold)
    painter.end()
    return pixmap

def credentials():
    try: return int(os.environ['TG_API_ID']),os.environ['TG_API_HASH']
    except (KeyError,ValueError): raise RuntimeError('Не заданы TG_API_ID и TG_API_HASH')

def download_latest_app_update():
    request_headers={'Accept':'application/vnd.github+json','User-Agent':'TG-Manager-Updater'}
    commit_url=f'https://api.github.com/repos/{UPDATE_REPOSITORY}/commits/{UPDATE_BRANCH}'
    try:
        request=urllib.request.Request(commit_url,headers=request_headers)
        with urllib.request.urlopen(request,timeout=20) as response:
            commit=json.loads(response.read(256*1024).decode('utf-8'))
    except urllib.error.HTTPError as error:
        if error.code==409:
            raise RuntimeError(f'В репозитории https://github.com/{UPDATE_REPOSITORY} пока нет коммитов. Сначала опубликуйте новую версию в ветке {UPDATE_BRANCH}.') from error
        if error.code==404:
            raise RuntimeError(f'Не найдена ветка {UPDATE_BRANCH} в репозитории https://github.com/{UPDATE_REPOSITORY}.') from error
        raise RuntimeError(f'GitHub не смог проверить обновления (HTTP {error.code}).') from error
    except (OSError,UnicodeError,json.JSONDecodeError) as error:
        raise RuntimeError(f'Не удалось проверить обновления на GitHub: {error}') from error

    commit_sha=commit.get('sha','')
    if not re.fullmatch(r'[0-9a-f]{40}',commit_sha):
        raise RuntimeError('GitHub вернул некорректный идентификатор версии.')

    def download_asset(path,limit,description):
        url=f'https://raw.githubusercontent.com/{UPDATE_REPOSITORY}/{commit_sha}/{path}'
        try:
            request=urllib.request.Request(url,headers={'User-Agent':'TG-Manager-Updater'})
            with urllib.request.urlopen(request,timeout=30) as response:
                data=response.read(limit+1)
        except urllib.error.HTTPError as error:
            if error.code==404: return None
            raise RuntimeError(f'Не удалось скачать {description} с GitHub (HTTP {error.code}).') from error
        except OSError as error:
            raise RuntimeError(f'Не удалось скачать {description} с GitHub: {error}') from error
        if len(data)>limit:
            raise RuntimeError(f'Файл {description} слишком большой; приложение оставлено без изменений.')
        return data

    source=None
    icon_data=None
    source_found=False
    for prefix in ('','github-update/'):
        candidate_source=download_asset(f'{prefix}app.py',4*1024*1024,'app.py')
        if candidate_source is None: continue
        source_found=True
        candidate_icon=download_asset(f'{prefix}tg-manager.ico',5*1024*1024,'иконку')
        if candidate_icon is None: continue
        source=candidate_source
        icon_data=candidate_icon
        break
    if source is None:
        if source_found:
            raise RuntimeError('Для найденного app.py не найден tg-manager.ico рядом с ним ни в корне репозитория, ни в папке github-update. Обновление не установлено.')
        raise RuntimeError('В корне репозитория и в папке github-update не найден app.py. Обновление не установлено.')
    try:
        source_text=source.decode('utf-8')
        compile(source_text,'app.py','exec')
    except (UnicodeError,SyntaxError) as error:
        raise RuntimeError(f'Файл app.py из GitHub не прошёл проверку и не будет установлен: {error}') from error
    if 'class Window(QMainWindow)' not in source_text or "if __name__=='__main__':" not in source_text:
        raise RuntimeError('Найденный в GitHub app.py не похож на TG Manager. Обновление отменено.')
    if len(icon_data)<22 or icon_data[:4]!=b'\x00\x00\x01\x00':
        raise RuntimeError('Файл tg-manager.ico из GitHub повреждён или имеет неподдерживаемый формат.')
    return commit_sha,source,icon_data

def account_issue_from_error(error):
    description=re.sub(r'[^A-Z0-9:]', '', f'{type(error).__name__}: {error}'.upper())
    if 'USERDEACTIVATEDERROR' in description and 'USERDEACTIVATEDBANERROR' not in description:
        return 'deleted'
    if any(marker in description for marker in ('USERDEACTIVATEDBANERROR','PHONENUMBERBANNEDERROR','ACCOUNTBANNEDERROR','ACCOUNTBANNED:')):
        return 'banned'
    if any(marker in description for marker in ('AUTHKEYUNREGISTEREDERROR','AUTHKEYINVALIDERROR','SESSIONREVOKEDERROR')):
        return 'invalid'
    if any(marker in description for marker in ('PEERFLOODERROR','USERRESTRICTEDERROR','ACCOUNTRESTRICTEDERROR','ACCOUNTRESTRICTED:')):
        return 'restricted'
    return ''

async def telegram_profile_snapshot(client,user):
    from telethon.tl.functions.users import GetFullUserRequest
    full=await telegram_call(client(GetFullUserRequest(user)),'получение данных профиля',timeout=20)
    AVATARS.mkdir(parents=True,exist_ok=True)
    avatar_path=AVATARS/f'{user.id}.jpg'
    avatar=''
    if user.photo:
        temp_path=AVATARS/f'.{user.id}.{time.time_ns()}.jpg'
        try:
            downloaded=await telegram_call(client.download_profile_photo(user,file=str(temp_path)),'загрузка фото профиля',timeout=30)
            if downloaded and Path(downloaded).is_file():
                os.replace(downloaded,avatar_path)
                avatar=str(avatar_path)
        finally:
            if temp_path.exists(): temp_path.unlink()
    elif avatar_path.exists():
        avatar_path.unlink()
    return user.first_name or '',user.last_name or '',user.username or '',avatar,getattr(full.full_user,'about','') or ''

def unsupported_tdata_python():
    return sys.version_info >= (3,14)

def normalize_proxy_url(proxy_url):
    raw=proxy_url.strip()
    if not raw or '://' in raw: return raw
    parts=raw.split(':',3)
    if len(parts)==2:
        host,port=parts
        if not host or not port.isdigit() or not 1<=int(port)<=65535:
            raise ValueError('Формат прокси без авторизации: IP:порт; порт должен быть от 1 до 65535.')
        return f'http://{host}:{int(port)}'
    if len(parts)!=4:
        raise ValueError('Поддерживаются форматы IP:порт (HTTP), IP:порт:логин:пароль (SOCKS5), socks5://… или http://….')
    host,port,username,password=parts
    if not host or not port.isdigit() or not 1<=int(port)<=65535 or not username or not password:
        raise ValueError('Формат прокси: IP:порт:логин:пароль; проверьте адрес, порт, логин и пароль.')
    return f'socks5://{quote(username,safe="")}:{quote(password,safe="")}@{host}:{int(port)}'

def configured_proxy_settings():
    try:
        settings=json.loads(SETTINGS.read_text(encoding='utf-8')) if SETTINGS.exists() else {}
    except (OSError,json.JSONDecodeError) as error:
        raise RuntimeError(f'Не удалось прочитать настройки подключения: {error}') from error
    proxy_url=(os.environ.get('TG_PROXY','') or settings.get('proxy','')).strip()
    environment_mode=os.environ.get('TG_PROXY_MODE','').strip()
    saved_mode=settings.get('proxy_mode','')
    if environment_mode in ('local','none','custom'):
        mode=environment_mode
    elif saved_mode in ('local','none','custom'):
        mode=saved_mode
    elif proxy_url:
        mode='custom'
    else:
        mode='local' if TGWS_PROXY_EXE.is_file() else 'none'
    return mode,proxy_url

def telegram_proxy(proxy_url=None):
    raw=(os.environ.get('TG_PROXY','') if proxy_url is None else proxy_url).strip()
    if not raw: return None
    raw=normalize_proxy_url(raw)
    try: parsed=urlsplit(raw); host=parsed.hostname; port=parsed.port
    except ValueError as error: raise ValueError('Не удалось разобрать адрес прокси. Проверьте формат.') from error
    if parsed.scheme not in ('socks5','socks5h','http') or not host or port is None:
        raise ValueError('Прокси задаётся как socks5://host:port или http://host:port; логин и пароль указываются как user:password@host.')
    if parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('В настройке прокси укажите только адрес, порт и, при необходимости, логин с паролем.')
    if not 1<=port<=65535:
        raise ValueError('Порт прокси должен быть от 1 до 65535.')
    try:
        import socks
    except ImportError as error:
        raise RuntimeError('Для работы с прокси установите зависимости проекта через install.bat.') from error
    proxy_type={'socks5':socks.SOCKS5,'socks5h':socks.SOCKS5,'http':socks.HTTP}[parsed.scheme]
    return (proxy_type,host,port,True,unquote(parsed.username) if parsed.username else None,unquote(parsed.password) if parsed.password else None)

def tgws_proxy_config():
    if not TGWS_PROXY_EXE.is_file():
        raise RuntimeError(f'Не найдены TgWsProxy_windows.exe и конфиг прокси в папке: {TGWS_PROXY_BIN}')
    if not TGWS_PROXY_CONFIG.is_file():
        if not TGWS_PROXY_BUNDLED_CONFIG.is_file():
            raise RuntimeError(f'Не найден конфиг TgWsProxy: {TGWS_PROXY_CONFIG}')
        try:
            TGWS_PROXY_CONFIG.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(TGWS_PROXY_BUNDLED_CONFIG,TGWS_PROXY_CONFIG)
        except OSError as error:
            raise RuntimeError(f'Не удалось подготовить конфиг TgWsProxy: {error}') from error
    try:
        config=json.loads(TGWS_PROXY_CONFIG.read_text(encoding='utf-8-sig'))
        host=config['host']; port=int(config['port']); secret=config['secret']
    except (OSError,json.JSONDecodeError,KeyError,TypeError,ValueError) as error:
        raise RuntimeError('Не удалось прочитать конфигурацию TgWsProxy (секрет скрыт).') from error
    if not isinstance(host,str) or not host or not 1<=port<=65535 or not isinstance(secret,str) or not secret:
        raise RuntimeError('В конфигурации TgWsProxy отсутствуют корректные host, port или secret.')
    return host,port,secret

def telegram_client_options(proxy_url=None,proxy_mode=None):
    configured_mode,configured_url=configured_proxy_settings()
    mode=proxy_mode or configured_mode
    if mode not in ('local','none','custom'):
        raise ValueError('Выберите способ подключения: локальный прокси, без прокси или пользовательский прокси.')
    raw=(proxy_url if proxy_url is not None else configured_url).strip()
    if mode=='none':
        return {}
    if mode=='custom':
        if not raw:
            raise ValueError('Введите адрес пользовательского прокси.')
        return {'proxy':telegram_proxy(raw)}
    host,port,secret=tgws_proxy_config()
    return {
        'connection':ConnectionTcpMTProxyRandomizedIntermediate,
        'proxy':(host,port,secret),
    }

def ensure_tgws_proxy():
    global _LOCAL_PROXY_PROCESS
    host,port,_=tgws_proxy_config()

    def is_listening():
        try:
            with socket.create_connection((host,port),timeout=0.5):
                return True
        except OSError:
            return False

    if is_listening():
        return
    if _LOCAL_PROXY_PROCESS is not None and _LOCAL_PROXY_PROCESS.poll() is not None:
        _LOCAL_PROXY_PROCESS=None
    if _LOCAL_PROXY_PROCESS is None:
        try:
            _LOCAL_PROXY_PROCESS=subprocess.Popen(
                [str(TGWS_PROXY_EXE)],
                cwd=str(TGWS_PROXY_BIN),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
            )
        except OSError as error:
            raise RuntimeError(f'Не удалось запустить локальный TgWsProxy: {error}') from error
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if is_listening():
            return
        if _LOCAL_PROXY_PROCESS.poll() is not None:
            _LOCAL_PROXY_PROCESS=None
            raise RuntimeError('TgWsProxy завершился при запуске. Проверьте его конфигурацию и доступность порта.')
        time.sleep(0.2)
    raise TimeoutError(f'TgWsProxy не открыл локальный порт {host}:{port} за 10 секунд.')

async def prepare_telegram_client():
    mode,_=configured_proxy_settings()
    if mode=='local':
        await asyncio.to_thread(ensure_tgws_proxy)
    return telegram_client_options(proxy_mode=mode)

async def connect_account_client(client,timeout=20):
    try:
        await asyncio.wait_for(client.connect(),timeout=timeout)
        authorized=await asyncio.wait_for(client.is_user_authorized(),timeout=timeout)
    except asyncio.TimeoutError as error:
        raise TimeoutError(f'Telegram не ответил за {timeout} секунд. Проверьте Интернет и выбранный прокси.') from error
    if not authorized:
        raise RuntimeError('Сессия аккаунта не авторизована. Повторно импортируйте TDATA этого аккаунта.')

async def telegram_call(awaitable,action,timeout=30):
    try:
        return await asyncio.wait_for(awaitable,timeout=timeout)
    except asyncio.TimeoutError as error:
        raise TimeoutError(f'Не удалось выполнить действие «{action}» за {timeout} секунд. Проверьте подключение к Telegram и повторите попытку.') from error

async def disconnect_account_client(client):
    await telegram_call(client.disconnect(),'закрытие соединения с Telegram',timeout=5)

def visible_error_message(error):
    if isinstance(error,str):
        return error.strip() or 'Операция завершилась ошибкой без описания. Повторите попытку и проверьте журнал приложения.'
    detail=str(error).strip() or repr(error).strip() or 'ошибка без дополнительного описания'
    return f'{type(error).__name__}: {detail}'

def create_account_client(session,api_id,api_hash,**options):
    client_options={'timeout':12,'connection_retries':1,'request_retries':1,'retry_delay':1}
    client_options.update(options)
    return TelegramClient(session,api_id,api_hash,**client_options)

class Worker(QObject):
    done=Signal(object); error=Signal(str); progress=Signal(str); item=Signal(object)
    def __init__(self,fn,with_progress=False): super().__init__(); self.fn=fn; self.with_progress=with_progress
    def run(self):
        loop=None
        try:
            operation=self.fn(self.progress.emit,self.item.emit) if self.with_progress else self.fn()
            loop=asyncio.SelectorEventLoop() if sys.platform=='win32' else asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result=loop.run_until_complete(operation)
            self.done.emit(result)
        except Exception as error:
            message=visible_error_message(error)
            write_import_log(f'Ошибка фоновой операции: {message}')
            self.error.emit(message)
        finally:
            if loop is not None:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)

class ImportWorker(QObject):
    done=Signal(object); error=Signal(str)
    def __init__(self,fn): super().__init__(); self.fn=fn
    def run(self):
        try: self.done.emit(self.fn())
        except Exception as e: self.error.emit(f'{type(e).__name__}: {e}')

class DialogSessionWorker(QObject):
    dialogs_batch=Signal(object); dialogs_loaded=Signal(int); history_loaded=Signal(str,str)
    message_sent=Signal(str,str,bool); message_received=Signal(str,str,str,str,int)
    private_unread_changed=Signal(str,int)
    status=Signal(str); failed=Signal(str); connected=Signal(str); stopped=Signal()
    def __init__(self,session,api_id,api_hash):
        super().__init__(); self.session=session; self.api_id=api_id; self.api_hash=api_hash
        self.loop=None; self.client=None; self.entities={}; self.private_unread={}; self.active_peer=None
        self._tasks=set(); self._closing=False; self._history_in_progress=False; self._history_cache={}

    def run(self):
        self.loop=asyncio.new_event_loop(); asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._initialize())
        self.loop.run_forever()
        pending=asyncio.all_tasks(self.loop)
        for task in pending: task.cancel()
        if pending: self.loop.run_until_complete(asyncio.gather(*pending,return_exceptions=True))
        self.loop.close(); self.stopped.emit()

    def _submit(self,coroutine):
        if self.loop is None or self._closing:
            coroutine.close()
            return
        def start():
            task=self.loop.create_task(coroutine); self._tasks.add(task); task.add_done_callback(self._tasks.discard)
        self.loop.call_soon_threadsafe(start)

    def load_history(self,peer):
        self._submit(self._load_history(peer))

    def set_active_peer(self,peer):
        self._submit(self._set_active_peer(peer))

    def refresh_history(self,peer):
        self._submit(self._refresh_history(peer))

    def send_message(self,peer,text):
        self._submit(self._send_message(peer,text))

    def stop(self):
        if self.loop is None or self._closing: return
        self._closing=True
        self.loop.call_soon_threadsafe(lambda:self.loop.create_task(self._shutdown()))

    async def _initialize(self):
        from telethon.tl.types import User
        from telethon import events
        from telethon.utils import get_peer_id
        try:
            self.status.emit('Подключаюсь к Telegram...')
            client_options=await prepare_telegram_client()
            self.client=create_account_client(self.session,self.api_id,self.api_hash,**client_options)
            await connect_account_client(self.client)
            self.connected.emit(self.session)
            self.client.add_event_handler(self._on_new_message,events.NewMessage(incoming=True))
            self.status.emit('Загружаю последние диалоги...')
            batch=[]
            async for dialog in self.client.iter_dialogs():
                entity=dialog.entity; peer=str(get_peer_id(entity)); self.entities[peer]=entity
                title=dialog.name or getattr(entity,'title','') or getattr(entity,'username','') or peer
                is_private=isinstance(entity,User)
                if is_private and not getattr(entity,'bot',False) and dialog.unread_count:
                    self.private_unread[peer]=dialog.unread_count
                batch.append((title,peer,dialog.unread_count,'personal' if is_private else 'groups'))
                if len(batch)>=25:
                    self.dialogs_batch.emit(batch); self.status.emit(f'Загружено диалогов: {len(self.entities)}...'); batch=[]
            if batch: self.dialogs_batch.emit(batch)
            self.private_unread_changed.emit(self.session,sum(self.private_unread.values()))
            self.dialogs_loaded.emit(len(self.entities))
        except Exception as error:
            self.failed.emit(f'{type(error).__name__}: {error}')

    async def _set_active_peer(self,peer):
        self.active_peer=peer

    async def _on_new_message(self,event):
        from telethon.tl.types import User
        from telethon.utils import get_peer_id
        try:
            peer=str(get_peer_id(event.message.peer_id))
            entity=self.entities.get(peer)
            if entity is None:
                entity=await event.get_chat()
                if entity is None: raise RuntimeError(f'Не удалось определить чат сообщения {peer}.')
                self.entities[peer]=entity
            is_private=isinstance(entity,User)
            category='personal' if is_private else 'groups'
            title=getattr(entity,'title',None)
            if not title and is_private:
                title=' '.join(part for part in (getattr(entity,'first_name',None),getattr(entity,'last_name',None)) if part)
            title=title or getattr(entity,'username',None) or peer
            unread_count=-1
            if is_private and not getattr(entity,'bot',False):
                if peer==self.active_peer:
                    try:
                        await self.client.send_read_acknowledge(entity)
                        self.private_unread.pop(peer,None)
                    except Exception as error:
                        self.private_unread[peer]=self.private_unread.get(peer,0)+1
                        self.failed.emit(f'Не удалось отметить личное сообщение прочитанным: {error}')
                else:
                    self.private_unread[peer]=self.private_unread.get(peer,0)+1
                unread_count=self.private_unread.get(peer,0)
                self.private_unread_changed.emit(self.session,sum(self.private_unread.values()))
            self.message_received.emit(peer,title,category,await self._format_message(event.message),unread_count)
        except Exception as error:
            self.failed.emit(f'{type(error).__name__}: {error}')

    async def _load_history(self,peer):
        self.active_peer=peer
        if self._history_in_progress: return
        self._history_in_progress=True
        try:
            self.status.emit('Загружаю сообщения...')
            entity=self.entities.get(peer)
            if entity is None: entity=await self.client.get_entity(int(peer))
            messages=await self.client.get_messages(entity,limit=50)
            lines=[await self._format_message(message) for message in reversed(messages)]
            if peer in self.private_unread:
                await self.client.send_read_acknowledge(entity)
                self.private_unread.pop(peer,None)
                self.private_unread_changed.emit(self.session,sum(self.private_unread.values()))
            history='\n\n'.join(lines) or 'В этом диалоге пока нет сообщений.'
            self._history_cache[peer]=history
            self.history_loaded.emit(peer,history)
            self.status.emit('История загружена.')
        except Exception as error:
            self.failed.emit(f'{type(error).__name__}: {error}')
        finally:
            self._history_in_progress=False

    async def _refresh_history(self,peer):
        if peer!=self.active_peer or self._history_in_progress: return
        self._history_in_progress=True
        try:
            entity=self.entities.get(peer)
            if entity is None: entity=await self.client.get_entity(int(peer))
            messages=await self.client.get_messages(entity,limit=50)
            lines=[await self._format_message(message) for message in reversed(messages)]
            if peer in self.private_unread:
                await self.client.send_read_acknowledge(entity)
                self.private_unread.pop(peer,None)
                self.private_unread_changed.emit(self.session,sum(self.private_unread.values()))
            if peer==self.active_peer:
                history='\n\n'.join(lines) or 'В этом диалоге пока нет сообщений.'
                if history!=self._history_cache.get(peer):
                    self._history_cache[peer]=history
                    self.history_loaded.emit(peer,history)
        except Exception as error:
            self.failed.emit(f'Не удалось обновить историю: {type(error).__name__}: {error}')
        finally:
            self._history_in_progress=False

    async def _send_message(self,peer,text):
        try:
            entity=self.entities.get(peer)
            if entity is None: entity=await self.client.get_entity(int(peer))
            self.status.emit('Отправляю сообщение...')
            message=await self.client.send_message(entity,text)
            self.message_sent.emit(peer,await self._format_message(message),True)
            self.status.emit('Сообщение отправлено.')
        except Exception as error:
            self.failed.emit(f'{type(error).__name__}: {error}')

    async def _format_message(self,message):
        date=message.date.astimezone().strftime('%Y-%m-%d %H:%M') if message.date else ''
        sender='Вы' if message.out else 'Собеседник'
        text=message.message or ''
        content=f'<p><b>[{date}] {sender}:</b>'
        if text:
            content+=f'<br/>{html.escape(text).replace(chr(10), "<br/>")}'
        media_kind=None
        media_label=''
        extension=''
        if getattr(message,'photo',None):
            media_kind='photo'; media_label='Фото'; extension='.jpg'
        elif getattr(message,'voice',None):
            media_kind='voice'; media_label='Голосовое сообщение — нажмите для воспроизведения'; extension='.ogg'
        elif getattr(message,'video_note',None):
            media_kind='video'; media_label='Видеокружок — нажмите для воспроизведения'; extension='.mp4'
        if media_kind:
            media_dir=DATA/'dialog_media'/hashlib.sha256(self.session.encode('utf-8')).hexdigest()[:20]
            media_dir.mkdir(parents=True,exist_ok=True)
            from telethon.utils import get_peer_id
            media_path=media_dir/f'{get_peer_id(message.peer_id)}_{message.id}{extension}'
            if not media_path.is_file() or media_path.stat().st_size==0:
                try:
                    downloaded=await self.client.download_media(message,file=str(media_path))
                    if not downloaded or not media_path.is_file() or media_path.stat().st_size==0:
                        raise RuntimeError('Telegram не вернул файл.')
                except Exception as error:
                    media_path.unlink(missing_ok=True)
                    write_import_log(f'Не удалось загрузить медиа сообщения {message.id}: {type(error).__name__}: {error}')
                    content+=f'<br/><i>[Не удалось загрузить {media_label.lower()}]</i>'
                else:
                    content+=self._media_html(media_kind,media_label,media_path)
            else:
                content+=self._media_html(media_kind,media_label,media_path)
        elif not text:
            content+='<br/><i>[Медиа- или служебное сообщение]</i>'
        return content+'</p>'

    @staticmethod
    def _media_html(media_kind,label,path):
        if media_kind=='photo':
            return f'<br/><img src="{path.resolve().as_uri()}" width="420"/>'
        encoded=base64.urlsafe_b64encode(str(path.resolve()).encode('utf-8')).decode('ascii').rstrip('=')
        return f'<br/><a href="tgmedia:{encoded}">▶ {html.escape(label)}</a>'

    def send_media(self,peer,path,kind):
        self._submit(self._send_media(peer,path,kind))

    async def _send_media(self,peer,path,kind):
        try:
            entity=self.entities.get(peer)
            if entity is None: entity=await self.client.get_entity(int(peer))
            self.status.emit({'photo':'Отправляю фото...','voice':'Отправляю голосовое...','video':'Отправляю видеокружок...'}[kind])
            options={'voice_note':kind=='voice','video_note':kind=='video'}
            message=await self.client.send_file(entity,path,**options)
            self.message_sent.emit(peer,await self._format_message(message),False)
            self.status.emit({'photo':'Фото отправлено.','voice':'Голосовое отправлено.','video':'Видеокружок отправлен.'}[kind])
        except Exception as error:
            self.failed.emit(f'Не удалось отправить медиа: {type(error).__name__}: {error}')

    async def _shutdown(self):
        current=asyncio.current_task()
        for task in tuple(self._tasks):
            if task is not current: task.cancel()
        if self.client:
            try: await asyncio.wait_for(self.client.disconnect(),timeout=2)
            except asyncio.TimeoutError: write_import_log('Тайм-аут при закрытии Telegram-соединения окна диалогов.')
            except Exception as error: write_import_log(f'Ошибка закрытия Telegram-соединения окна диалогов: {error}')
        self.loop.stop()

class DialogMessageInput(QPlainTextEdit):
    sendRequested=Signal()
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Return,Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

class DialogMediaPlayer(QDialog):
    def __init__(self,path,video,parent=None):
        super().__init__(parent); self.setWindowTitle('Видеокружок' if video else 'Голосовое сообщение')
        self.resize(480,360 if video else 140)
        layout=QVBoxLayout(self); self.video=QVideoWidget(self); layout.addWidget(self.video)
        self.status=QLabel('Загрузка медиа...'); layout.addWidget(self.status)
        self.play_button=QPushButton('Пауза'); layout.addWidget(self.play_button)
        self.player=QMediaPlayer(self); self.audio=QAudioOutput(self); self.player.setAudioOutput(self.audio)
        if video: self.player.setVideoOutput(self.video)
        else: self.video.hide()
        self.player.errorOccurred.connect(lambda error,message:self.status.setText(f'Не удалось воспроизвести: {message}'))
        self.player.playbackStateChanged.connect(self._playback_changed)
        self.play_button.clicked.connect(self._toggle_playback)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def _toggle_playback(self):
        if self.player.playbackState()==QMediaPlayer.PlayingState: self.player.pause()
        else: self.player.play()

    def _playback_changed(self,state):
        self.play_button.setText('Пауза' if state==QMediaPlayer.PlayingState else 'Воспроизвести')
        if state==QMediaPlayer.PlayingState: self.status.setText('')


class TelegramDialogsWindow(QDialog):
    privateUnreadChanged=Signal(str,int)
    def __init__(self,name,session,api_id,api_hash,parent=None):
        super().__init__(parent); self.session=session; self._closing=False; self._thread=QThread(self)
        self.setWindowTitle(f'Диалоги — {name}'); self.resize(900,650); self.setAttribute(Qt.WA_DeleteOnClose)
        layout=QVBoxLayout(self); layout.addWidget(QLabel(f'Переписки аккаунта: {name}'))
        self.status_label=QLabel('Подключение к Telegram...'); self.status_label.setObjectName('muted'); layout.addWidget(self.status_label)
        content=QHBoxLayout(); self.dialog_tabs=QTabWidget(); self.dialog_tabs.setMinimumWidth(250); self.dialog_tabs.setMaximumWidth(360)
        self.personal_dialog_list=QListWidget(); self.group_dialog_list=QListWidget()
        self.personal_dialog_list.currentItemChanged.connect(lambda item,previous:self._dialog_selected(item,'personal'))
        self.group_dialog_list.currentItemChanged.connect(lambda item,previous:self._dialog_selected(item,'groups'))
        self.dialog_tabs.currentChanged.connect(self._active_tab_changed)
        self.dialog_tabs.addTab(self.personal_dialog_list,'Личные')
        self.dialog_tabs.addTab(self.group_dialog_list,'Общие чаты')
        conversation=QVBoxLayout(); self.title_label=QLabel('Выберите диалог'); self.title_label.setObjectName('title'); conversation.addWidget(self.title_label)
        self.history=QTextBrowser(); self.history.setOpenExternalLinks(False); self.history.anchorClicked.connect(self._open_media)
        self.history.setPlaceholderText('История сообщений появится здесь.'); conversation.addWidget(self.history,1)
        self.history_refresh_timer=QTimer(self); self.history_refresh_timer.setInterval(2000)
        self.history_refresh_timer.timeout.connect(self._refresh_open_history)
        self.compose=DialogMessageInput(); self.compose.setPlaceholderText('Введите ответ... (Enter — отправить, Shift+Enter — новая строка)'); self.compose.setMaximumHeight(100); self.compose.sendRequested.connect(self._send); conversation.addWidget(self.compose)
        row=QHBoxLayout()
        self.photo_button=QPushButton('Фото'); self.photo_button.clicked.connect(lambda:self._choose_media('photo')); row.addWidget(self.photo_button)
        self.voice_button=QPushButton('Голосовое'); self.voice_button.clicked.connect(lambda:self._choose_media('voice')); row.addWidget(self.voice_button)
        self.video_button=QPushButton('Кружок'); self.video_button.clicked.connect(lambda:self._choose_media('video')); row.addWidget(self.video_button)
        row.addStretch(); self.send_button=QPushButton('➤ Отправить'); self.send_button.setObjectName('primary'); self.send_button.clicked.connect(self._send); row.addWidget(self.send_button); conversation.addLayout(row)
        content.addWidget(self.dialog_tabs); content.addLayout(conversation,1); layout.addLayout(content,1)
        self.worker=DialogSessionWorker(session,api_id,api_hash); self.worker.moveToThread(self._thread)
        self._thread.started.connect(self.worker.run); self.worker.status.connect(self.status_label.setText)
        self.worker.dialogs_batch.connect(self._add_dialogs); self.worker.dialogs_loaded.connect(self._dialogs_loaded)
        self.worker.history_loaded.connect(self._history_loaded); self.worker.message_sent.connect(self._message_sent)
        self.worker.message_received.connect(self._message_received)
        self.worker.private_unread_changed.connect(self.privateUnreadChanged)
        self.worker.connected.connect(self._connected)
        self.worker.failed.connect(self._failed); self.worker.stopped.connect(self._thread.quit)
        self._thread.finished.connect(self.worker.deleteLater); self._thread.finished.connect(self._thread_stopped)
        self.send_button.setEnabled(False); self.photo_button.setEnabled(False); self.voice_button.setEnabled(False); self.video_button.setEnabled(False)
        self.personal_dialog_list.setEnabled(False); self.group_dialog_list.setEnabled(False); self._thread.start()

    def _add_dialogs(self,dialogs):
        for title,peer,unread,category in dialogs:
            target=self.personal_dialog_list if category=='personal' else self.group_dialog_list
            item=self._find_dialog_item(peer,target)
            if item is None:
                item=QListWidgetItem(); item.setData(Qt.UserRole,peer); item.setData(Qt.UserRole+1,title)
                item.setData(Qt.UserRole+2,unread); item.setData(Qt.UserRole+3,'')
                target.addItem(item)
            self._update_dialog_label(item)
        self.personal_dialog_list.setEnabled(True); self.group_dialog_list.setEnabled(True)
        self._set_media_enabled(True)
        if self.personal_dialog_list.count(): self.dialog_tabs.setCurrentWidget(self.personal_dialog_list)
        elif self.group_dialog_list.count(): self.dialog_tabs.setCurrentWidget(self.group_dialog_list)
        current=self._current_dialog_item()
        if current is None:
            source=self.personal_dialog_list if self.personal_dialog_list.count() else self.group_dialog_list
            if source.count(): source.setCurrentRow(0)

    def _connected(self,session):
        self.status_label.setText('Подключено. Загружаю диалоги...')

    def _dialogs_loaded(self,count):
        self.status_label.setText(f'Загружено диалогов: {count}.')
        self.personal_dialog_list.setEnabled(True); self.group_dialog_list.setEnabled(True)
        self._set_media_enabled(True)
        if count and self._current_dialog_item() is None:
            source=self.personal_dialog_list if self.personal_dialog_list.count() else self.group_dialog_list
            if source.count(): source.setCurrentRow(0)
        elif not count: self.history.setPlainText('Диалогов не найдено.')

    def _current_dialog_item(self):
        source=self.dialog_tabs.currentWidget()
        return source.currentItem() if isinstance(source,QListWidget) else None

    @staticmethod
    def _find_dialog_item(peer,dialog_list):
        for index in range(dialog_list.count()):
            item=dialog_list.item(index)
            if item.data(Qt.UserRole)==peer: return item
        return None

    @staticmethod
    def _update_dialog_label(item):
        title=item.data(Qt.UserRole+1) or item.data(Qt.UserRole)
        preview=item.data(Qt.UserRole+3)
        unread=item.data(Qt.UserRole+2) or 0
        parts=[title]
        if preview: parts.append(preview)
        if unread: parts.append(f'{unread} непрочитанных')
        item.setText(' · '.join(parts))

    def _set_media_enabled(self,enabled):
        self.send_button.setEnabled(enabled)
        self.photo_button.setEnabled(enabled)
        self.voice_button.setEnabled(enabled)
        self.video_button.setEnabled(enabled)

    def _choose_media(self,kind):
        item=self._current_dialog_item()
        if item is None:
            self.status_label.setText('Сначала выберите диалог.')
            return
        filters={
            'photo':'Изображения (*.png *.jpg *.jpeg *.webp *.bmp);;Все файлы (*.*)',
            'voice':'Голосовые (*.ogg *.oga);;Аудиофайлы (*.mp3 *.m4a *.wav);;Все файлы (*.*)',
            'video':'Видеофайлы (*.mp4 *.mov *.webm);;Все файлы (*.*)',
        }
        labels={'photo':'Выберите фото','voice':'Выберите файл голосового сообщения','video':'Выберите видео для кружка'}
        path,_=QFileDialog.getOpenFileName(self,labels[kind],'',filters[kind])
        if not path: return
        self._set_media_enabled(False)
        self.worker.send_media(item.data(Qt.UserRole),path,kind)

    def _open_media(self,url):
        if url.scheme()!='tgmedia': return
        token=url.toString().split(':',1)[1]
        try:
            token+=('='*((4-len(token)%4)%4))
            path=Path(base64.urlsafe_b64decode(token.encode('ascii')).decode('utf-8')).resolve()
            media_root=(DATA/'dialog_media').resolve()
            if not path.is_relative_to(media_root) or not path.is_file():
                raise ValueError('Файл медиа не найден в локальном кэше.')
            player=DialogMediaPlayer(path,path.suffix.lower() in ('.mp4','.mov','.webm'),self)
            player.show()
            self._media_players=getattr(self,'_media_players',[])
            self._media_players.append(player)
            player.destroyed.connect(lambda _,window=player:self._media_players.remove(window) if window in self._media_players else None)
        except Exception as error:
            self.status_label.setText(f'Не удалось открыть медиа: {error}')

    def _message_received(self,peer,title,category,rendered,unread_count):
        dialog_list=self.personal_dialog_list if category=='personal' else self.group_dialog_list
        active_item=self._current_dialog_item()
        active_peer=active_item.data(Qt.UserRole) if active_item is not None else None
        list_item=dialog_list.currentItem()
        list_peer=list_item.data(Qt.UserRole) if list_item is not None else None
        dialog_list.blockSignals(True)
        try:
            item=self._find_dialog_item(peer,dialog_list)
            if item is None:
                item=QListWidgetItem(); item.setData(Qt.UserRole,peer); item.setData(Qt.UserRole+1,title)
                item.setData(Qt.UserRole+2,0); item.setData(Qt.UserRole+3,'')
                dialog_list.insertItem(0,item)
            item.setData(Qt.UserRole+1,title)
            message_text=html.unescape(re.sub(r'<[^>]*>',' ',rendered))
            message_text=' '.join(message_text.split())
            item.setData(Qt.UserRole+3,message_text[:48] + ('…' if len(message_text)>48 else ''))
            if unread_count>=0: item.setData(Qt.UserRole+2,unread_count)
            item.setToolTip(f'{peer}\n{rendered}')
            self._update_dialog_label(item)
            row=dialog_list.row(item)
            if row>0:
                item=dialog_list.takeItem(row)
                dialog_list.insertItem(0,item)
            if list_peer is not None:
                selected=self._find_dialog_item(list_peer,dialog_list)
                if selected is not None: dialog_list.setCurrentItem(selected)
            elif dialog_list.currentItem() is not None:
                dialog_list.setCurrentItem(None)
        finally:
            dialog_list.blockSignals(False)
        if active_peer==peer:
            current=self.history.toHtml()
            if rendered not in current:
                self.history.moveCursor(QTextCursor.End); self.history.insertHtml(rendered)
                self.history.moveCursor(QTextCursor.End)
                self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

    def _active_tab_changed(self,index):
        self._dialog_selected(self._current_dialog_item(),'personal' if index==0 else 'groups')

    def _dialog_selected(self,item,category):
        if item is None:
            self.history_refresh_timer.stop()
            self.worker.set_active_peer(None)
            return
        self.history_refresh_timer.start()
        self.title_label.setText(item.text().split(' · ',1)[0])
        self.history.setPlainText('Загружаю историю...')
        self._set_media_enabled(False)
        self.worker.load_history(item.data(Qt.UserRole))

    def _refresh_open_history(self):
        item=self._current_dialog_item()
        if item is not None and self.send_button.isEnabled():
            self.worker.refresh_history(item.data(Qt.UserRole))

    def _history_loaded(self,peer,text):
        item=self._current_dialog_item()
        if item is None or item.data(Qt.UserRole)!=peer: return
        self.history.setHtml(text)
        self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())
        self._set_media_enabled(True)

    def _send(self):
        if not self.send_button.isEnabled(): return
        item=self._current_dialog_item(); text=self.compose.toPlainText().strip()
        if item is None:
            self.status_label.setText('Сначала выберите диалог.')
            return
        if not text:
            self.status_label.setText('Введите текст сообщения.')
            return
        self._set_media_enabled(False)
        self.worker.send_message(item.data(Qt.UserRole),text)

    def _message_sent(self,peer,rendered,clear_compose):
        item=self._current_dialog_item()
        if item is not None and item.data(Qt.UserRole)==peer:
            self.history.moveCursor(QTextCursor.End); self.history.insertHtml(rendered)
            self.history.moveCursor(QTextCursor.End)
            self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())
            if clear_compose: self.compose.clear()
        self._set_media_enabled(True)

    def _failed(self,error):
        self.status_label.setText(f'Ошибка Telegram: {error}')
        self._set_media_enabled(True)

    def closeEvent(self,event):
        self.history_refresh_timer.stop()
        if not self._closing and self._thread.isRunning():
            self._closing=True; self.status_label.setText('Закрываю соединение с Telegram...')
            self.worker.stop(); event.ignore()
            return
        if self._thread.isRunning():
            event.ignore(); return
        event.accept()

    def _thread_stopped(self):
        if self._closing: self.close()

class Window(QMainWindow):
    def __init__(self):
        super().__init__(); self.conn=database(); self.thread=None; self.profile_thread=None; self.proxy_test_thread=None
        self.profile_edit_thread=None
        self.update_thread=None
        self.update_check_only=False; self.update_available=False; self.update_notice_shown=False
        self.chat_send_thread=None; self.chatlist_thread=None
        self.dialog_windows={}; self.dialog_connected_sessions=set(); self.account_unread_counts={}; self.account_unread_badges={}
        self.unread_poll_thread=None
        self.chat_folder_save_error=None; self.chat_folder_imported_count=0; self.chat_folder_import_session=''
        self.setWindowTitle('TG Manager'); self.setWindowIcon(QIcon(str(ROOT/'tg-manager.ico'))); self.resize(1180,760); self.build(); self.refresh()
        self.unread_timer=QTimer(self); self.unread_timer.setInterval(60000); self.unread_timer.timeout.connect(self.refresh_private_unread); self.unread_timer.start()
        QTimer.singleShot(1500,lambda:self.refresh_profiles(show_errors=False))
        QTimer.singleShot(4000,self.refresh_private_unread)
        self.update_check_timer=QTimer(self); self.update_check_timer.setInterval(UPDATE_CHECK_INTERVAL_MS); self.update_check_timer.timeout.connect(self.check_for_updates); self.update_check_timer.start()
        QTimer.singleShot(2500,self.check_for_updates)
    def heading(self,title,subtitle):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,0,0,18)
        a=QLabel(title); a.setObjectName('title'); b=QLabel(subtitle); b.setObjectName('muted'); l.addWidget(a); l.addWidget(b); return w
    def make_table(self,headers):
        t=QTableWidget(0,len(headers)); t.setHorizontalHeaderLabels(headers); t.verticalHeader().hide(); t.setSelectionBehavior(QTableWidget.SelectRows); t.setEditTriggers(QAbstractItemView.NoEditTriggers); t.setAlternatingRowColors(True); t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); return t
    def create_metric(self,label):
        f=QFrame(); f.setObjectName('card'); l=QVBoxLayout(f); a=QLabel(label.upper()); a.setObjectName('muted'); v=QLabel('0'); v.setObjectName('metric'); l.addWidget(a); l.addWidget(v); return f
    def update_application(self):
        if self.update_thread and self.update_thread.isRunning():
            self.status.setText('Проверка обновления уже выполняется.')
            return
        self.update_check_only=False
        self.update_button.setEnabled(False)
        self.status.setText('Проверяю актуальную версию в GitHub...')
        self.start_update_worker()

    def check_for_updates(self):
        if self.update_thread and self.update_thread.isRunning():
            return
        self.update_check_only=True
        self.start_update_worker()

    def start_update_worker(self):
        thread=QThread(self)
        worker=Worker(lambda:asyncio.to_thread(download_latest_app_update))
        self.update_thread=thread; self.update_worker=worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self.update_downloaded)
        worker.error.connect(self.update_failed)
        worker.done.connect(thread.quit); worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda finished=thread:self.update_thread_finished(finished))
        thread.start()

    def update_thread_finished(self,thread):
        if self.update_thread is thread: self.update_thread=None
        thread.deleteLater()

    def update_failed(self,error,silent_check=False):
        silent_check=silent_check or self.update_check_only
        self.update_check_only=False
        self.update_button.setEnabled(True)
        self.status.setText('Не удалось проверить обновления.' if silent_check else 'Не удалось обновить приложение.')
        if silent_check:
            write_import_log(f'Не удалось проверить наличие обновлений: {error}')
        else:
            QMessageBox.warning(self,'Обновление TG Manager',str(error))

    def update_downloaded(self,result):
        commit_sha,source,icon_data=result
        silent_check=self.update_check_only
        self.update_check_only=False
        current_app=Path(__file__).resolve()
        current_icon=ROOT/'tg-manager.ico'
        try:
            if current_app.read_bytes()==source and current_icon.read_bytes()==icon_data:
                self.update_button.setEnabled(True)
                self.update_available=False
                self.update_button.setText('⟳  Обновить версию')
                self.update_button.setObjectName('')
                self.update_button.setToolTip('')
                self.update_button.style().unpolish(self.update_button)
                self.update_button.style().polish(self.update_button)
                if not silent_check:
                    self.status.setText('Установлена последняя версия.')
                    QMessageBox.information(self,'Обновление TG Manager','Установлена последняя версия приложения.')
                return
        except OSError as error:
            self.update_failed(f'Не удалось прочитать установленный app.py: {error}',silent_check=silent_check)
            return
        if silent_check:
            self.update_available=True
            self.update_button.setEnabled(True)
            self.update_button.setText('⬆  Доступна новая версия')
            self.update_button.setObjectName('primary')
            self.update_button.setToolTip(f'Доступно обновление из GitHub ({commit_sha[:7]}). Нажмите, чтобы установить.')
            self.update_button.style().unpolish(self.update_button)
            self.update_button.style().polish(self.update_button)
            self.status.setText('В GitHub доступна новая версия приложения.')
            if not self.update_notice_shown:
                self.update_notice_shown=True
                QMessageBox.information(
                    self,
                    'Доступна новая версия TG Manager',
                    f'В GitHub опубликовано обновление (коммит {commit_sha[:7]}). Нажмите «Доступна новая версия» в боковой панели, чтобы установить его.',
                )
            return
        answer=QMessageBox.question(
            self,
            'Доступно обновление',
            f'Найдена новая версия из GitHub (коммит {commit_sha[:7]}). Установить её и перезапустить программу?',
            QMessageBox.Yes|QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer!=QMessageBox.Yes:
            self.update_button.setEnabled(True)
            self.status.setText('Обновление отменено.')
            return
        temporary=current_app.with_name('app.py.update')
        temporary_icon=current_icon.with_name('tg-manager.ico.update')
        backup=DATA/'app.py.update-backup'
        backup_icon=DATA/'tg-manager.ico.update-backup'
        try:
            temporary.write_bytes(source)
            temporary_icon.write_bytes(icon_data)
            backup.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(current_app,backup)
            shutil.copy2(current_icon,backup_icon)
            os.replace(temporary,current_app)
            os.replace(temporary_icon,current_icon)
            subprocess.Popen(
                [sys.executable,str(current_app)],
                cwd=str(ROOT),
                env=os.environ.copy(),
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
            )
        except OSError as error:
            try:
                if backup.is_file(): shutil.copy2(backup,current_app)
                if backup_icon.is_file(): shutil.copy2(backup_icon,current_icon)
            except OSError as rollback_error:
                error=RuntimeError(f'{error} Не удалось восстановить предыдущую версию: {rollback_error}')
            temporary.unlink(missing_ok=True)
            temporary_icon.unlink(missing_ok=True)
            self.update_failed(f'Не удалось установить обновление: {error}')
            return
        self.status.setText('Обновление установлено. Перезапускаю приложение...')
        QApplication.quit()
    def build(self):
        root=QHBoxLayout(); root.setContentsMargins(0,0,0,0); root.setSpacing(0); container=QWidget(); container.setLayout(root)
        side=QFrame(); side.setObjectName('side'); side.setFixedWidth(245); sl=QVBoxLayout(side); sl.setContentsMargins(18,25,18,18)
        brand=QWidget(); brand_layout=QHBoxLayout(brand); brand_layout.setContentsMargins(0,0,0,0); brand_layout.setSpacing(8)
        brand_icon=QLabel(); brand_icon.setPixmap(telegram_mark_icon(24)); brand_layout.addWidget(brand_icon)
        brand_text=QLabel('TG MANAGER'); brand_text.setObjectName('brand'); brand_layout.addWidget(brand_text); brand_layout.addStretch()
        sl.addWidget(brand); sl.addSpacing(25)
        self.nav=QListWidget(); self.nav.addItems(['Обзор','Аккаунты','Чаты','Отправить сообщение']); self.nav.setObjectName('nav'); sl.addWidget(self.nav); sl.addStretch()
        api=QPushButton('⚙  Настройки API'); api.clicked.connect(self.api_settings); sl.addWidget(api)
        self.logs_button=QPushButton('▤  Логи'); self.logs_button.setMaximumHeight(30); self.logs_button.setStyleSheet('text-align:left;padding:5px 10px;color:#8c99ad;font-size:11px;'); self.logs_button.clicked.connect(self.show_logs); sl.addWidget(self.logs_button)
        self.update_button=QPushButton('⟳  Обновить версию'); self.update_button.clicked.connect(self.update_application); sl.addWidget(self.update_button)
        exit_button=QPushButton('⏻  Выход'); exit_button.clicked.connect(QApplication.quit); sl.addWidget(exit_button)
        sl.addWidget(QLabel('Локальное управление\nTelegram-аккаунтами'))
        self.pages=QStackedWidget(); self.pages.addWidget(self.dashboard()); self.pages.addWidget(self.accounts_page()); self.pages.addWidget(self.chats_page()); self.pages.addWidget(self.send_page()); self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        root.addWidget(side); root.addWidget(self.pages); self.setCentralWidget(container); self.nav.setCurrentRow(0)
        write_import_log('TG Manager запущен')

    def show_logs(self):
        dialog=QDialog(self); dialog.setWindowTitle('Журнал приложения'); dialog.resize(850,560)
        layout=QVBoxLayout(dialog)
        note=QLabel('Здесь отображаются события и ошибки приложения. API Hash и пароль прокси маскируются; перед отправкой журнала всё равно проверьте его содержимое.')
        note.setWordWrap(True); note.setObjectName('muted'); layout.addWidget(note)
        log_view=QPlainTextEdit(); log_view.setReadOnly(True); log_view.setLineWrapMode(QPlainTextEdit.NoWrap); layout.addWidget(log_view,1)
        buttons=QHBoxLayout()
        refresh_button=QPushButton('Обновить'); copy_button=QPushButton('Копировать все логи'); close_button=QPushButton('Закрыть')
        buttons.addWidget(refresh_button); buttons.addStretch(); buttons.addWidget(copy_button); buttons.addWidget(close_button); layout.addLayout(buttons)

        def load_logs():
            entries=[]
            for path in (LOG.with_suffix(LOG.suffix+'.1'),LOG):
                if not path.exists(): continue
                try:
                    entries.append(sanitize_log_text(path.read_text(encoding='utf-8')))
                except (OSError,UnicodeError) as error:
                    QMessageBox.warning(dialog,'Журнал приложения',f'Не удалось прочитать {path.name}: {error}')
                    return
            content=''.join(entries).strip()
            log_view.setPlainText(content or 'Записей пока нет.')
            log_view.verticalScrollBar().setValue(log_view.verticalScrollBar().maximum())

        def copy_logs():
            content=log_view.toPlainText()
            if not content or content=='Записей пока нет.':
                QMessageBox.information(dialog,'Журнал приложения','Пока нечего копировать.')
                return
            QApplication.clipboard().setText(content)
            copy_button.setText('Скопировано')
            QTimer.singleShot(1800,lambda:copy_button.setText('Копировать все логи'))

        refresh_button.clicked.connect(load_logs)
        copy_button.clicked.connect(copy_logs)
        close_button.clicked.connect(dialog.accept)
        load_logs()
        dialog.exec()
    def dashboard(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(34,30,34,30); l.addWidget(self.heading('Добро пожаловать','Управляйте своими аккаунтами и отправляйте сообщения вручную'))
        g=QGridLayout(); self.accounts_metric=self.create_metric('Аккаунты'); self.chats_metric=self.create_metric('Чаты'); g.addWidget(self.accounts_metric,0,0); g.addWidget(self.chats_metric,0,1); g.addWidget(self.create_metric('Режим'),0,2); l.addLayout(g); l.addSpacing(24); l.addWidget(QLabel('Быстрые действия'))
        for text,row in [('＋  Импортировать TDATA',1),('＋  Добавить чат',2),('✎  Новое сообщение',3)]:
            b=QPushButton(text); b.setObjectName('action'); b.clicked.connect(lambda _,r=row:self.nav.setCurrentRow(r)); l.addWidget(b)
        l.addStretch(); return w
    def accounts_page(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(34,30,34,30); h=QHBoxLayout(); h.addWidget(self.heading('Аккаунты','Импортируйте профили Telegram Desktop; имя и статус обновляются из Telegram')); refresh=QPushButton('⟳ Обновить профили'); refresh.clicked.connect(self.refresh_profiles); h.addWidget(refresh,0,Qt.AlignTop); b=QPushButton('＋ Импорт ZIP'); b.clicked.connect(self.import_zip); h.addWidget(b,0,Qt.AlignTop); folder=QPushButton('Импорт папки TDATA'); folder.clicked.connect(self.import_folder); h.addWidget(folder,0,Qt.AlignTop); l.addLayout(h); self.import_status=QLabel('Выберите ZIP-архив или папку tdata'); self.import_status.setObjectName('muted'); l.addWidget(self.import_status);         self.account_table=self.make_table(['Фото','Имя аккаунта','Username','Файл сессии','Статус','Профиль','Диалоги','Действия']); self.account_table.setIconSize(QPixmap(40,40).size()); l.addWidget(self.account_table); return w
    def chats_page(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(34,30,34,30)
        h=QHBoxLayout(); h.addWidget(self.heading('Чаты','Назначьте аккаунт каждому чату и отправляйте сообщения отдельно или всем списком')); self.chat_folder_button=QPushButton('＋ Импорт папки чатов'); self.chat_folder_button.clicked.connect(self.import_chat_folder); h.addWidget(self.chat_folder_button,0,Qt.AlignTop); b=QPushButton('＋ Добавить группу'); b.clicked.connect(self.add_chat); h.addWidget(b,0,Qt.AlignTop); self.chat_broadcast_button=QPushButton('Авторассылка'); self.chat_broadcast_button.setObjectName('primary'); self.chat_broadcast_button.setToolTip('Отправить сообщение по умолчанию во все сохранённые чаты'); self.chat_broadcast_button.clicked.connect(self.send_to_all_chats); h.addWidget(self.chat_broadcast_button,0,Qt.AlignTop); l.addLayout(h)
        self.chat_status=QLabel('Для каждого чата выберите аккаунт. «Авторассылка» отправит сообщение по умолчанию во все чаты списка.'); self.chat_status.setObjectName('muted'); l.addWidget(self.chat_status)
        self.chat_progress=QProgressBar(); self.chat_progress.setRange(0,0); self.chat_progress.setTextVisible(False); self.chat_progress.setMaximumHeight(8); self.chat_progress.hide(); l.addWidget(self.chat_progress)
        default_box=QFrame(); default_box.setObjectName('panel'); default_layout=QVBoxLayout(default_box)
        default_layout.addWidget(QLabel('Сообщение по умолчанию'))
        self.default_message=QPlainTextEdit(); self.default_message.setPlaceholderText('Этот текст можно отправить в выбранный чат или во все чаты кнопкой «Авторассылка».'); self.default_message.setMaximumHeight(110)
        try:
            settings=json.loads(SETTINGS.read_text(encoding='utf-8')) if SETTINGS.exists() else {}
            self.default_message.setPlainText(settings.get('default_message',''))
        except (OSError,json.JSONDecodeError) as error:
            self.chat_status.setText(f'Не удалось загрузить сохранённое сообщение: {error}')
        default_layout.addWidget(self.default_message)
        save_default=QPushButton('Сохранить сообщение по умолчанию'); save_default.clicked.connect(self.save_default_message); default_layout.addWidget(save_default,0,Qt.AlignRight)
        l.addWidget(default_box)
        self.chat_table=self.make_table(['Название','Идентификатор','Тип','Аккаунт отправки','Действие'])
        self.chat_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.chat_table.customContextMenuRequested.connect(self.show_chat_context_menu)
        l.addWidget(self.chat_table); return w

    def send_page(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(34,30,34,30); l.addWidget(self.heading('Новое сообщение','Отправка выполняется только после ручного подтверждения'))
        box=QFrame(); box.setObjectName('panel'); f=QFormLayout(box); self.account_box=QComboBox(); self.chat_box=QComboBox(); self.message=QPlainTextEdit(); self.message.setPlaceholderText('Введите текст сообщения...'); self.message.setMinimumHeight(180); f.addRow('Отправитель',self.account_box); f.addRow('Получатель',self.chat_box); f.addRow('Текст',self.message); l.addWidget(box)
        send=QPushButton('➤  Отправить сообщение'); send.setObjectName('primary'); send.clicked.connect(self.send_message); l.addWidget(send,0,Qt.AlignRight); self.status=QLabel('Готово к работе'); self.status.setObjectName('muted'); l.addWidget(self.status); l.addStretch(); return w
    def start_dialog_task(self,task,status,completed):
        if self.dialog_thread and self.dialog_thread.isRunning():
            self.dialog_status.setText('Дождитесь завершения текущей операции с диалогами.')
            return
        self.dialog_status.setText(status)
        self.dialog_account_box.setEnabled(False); self.dialog_load_button.setEnabled(False); self.dialog_send_button.setEnabled(False); self.dialog_list.setEnabled(False)
        thread=QThread(self); worker=Worker(lambda progress,item:task(progress),with_progress=True)
        self.dialog_thread=thread; self.dialog_worker=worker
        worker.moveToThread(thread); thread.started.connect(worker.run)
        worker.progress.connect(self.dialog_status.setText)
        session=self.dialog_account_box.currentData()
        worker.done.connect(completed); worker.error.connect(lambda error:self.dialog_operation_failed(error,session),Qt.QueuedConnection)
        worker.done.connect(thread.quit,Qt.DirectConnection); worker.error.connect(thread.quit,Qt.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda finished=thread:self.dialog_thread_finished(finished))
        thread.start()

    def dialog_thread_finished(self,thread):
        if self.dialog_thread is thread: self.dialog_thread=None
        self.dialog_account_box.setEnabled(True); self.dialog_load_button.setEnabled(True); self.dialog_send_button.setEnabled(True); self.dialog_list.setEnabled(True)
        thread.deleteLater()
        if self.dialog_first_load_pending:
            self.dialog_first_load_pending=False
            self.dialog_selected(self.dialog_list.currentItem())

    def dialog_account_changed(self,index):
        self.dialog_first_load_pending=False
        self.dialog_list.clear(); self.dialog_history.clear(); self.dialog_title.setText('Выберите диалог')
        self.dialog_status.setText('Загрузите диалоги для выбранного аккаунта.' if index>=0 else 'Добавьте аккаунт Telegram.')

    def dialog_operation_failed(self,error,session=''):
        self.mark_account_issue(session,error)
        self.dialog_status.setText(f'Ошибка Telegram: {error}')

    def load_dialogs(self):
        session=self.dialog_account_box.currentData()
        if not session:
            self.dialog_status.setText('Сначала импортируйте или выберите аккаунт.')
            return
        try: api_id,api_hash=credentials()
        except Exception as error:
            self.dialog_status.setText(f'Ошибка настроек API: {error}')
            return
        self.dialog_list.clear(); self.dialog_history.clear(); self.dialog_title.setText('Выберите диалог')
        self.start_dialog_task(lambda progress:self.fetch_dialogs(api_id,api_hash,session,progress),'Подключаюсь и загружаю диалоги...',self.dialogs_loaded)

    async def fetch_dialogs(self,api_id,api_hash,session,progress):
        from telethon.utils import get_peer_id
        client_options=await prepare_telegram_client()
        client=create_account_client(session,api_id,api_hash,**client_options)
        try:
            progress('Подключаю аккаунт и загружаю список диалогов...')
            await connect_account_client(client)
            results=[]
            async for dialog in client.iter_dialogs():
                entity=dialog.entity
                peer=str(get_peer_id(entity))
                title=dialog.name or getattr(entity,'title','') or getattr(entity,'username','') or peer
                results.append((title,peer,dialog.unread_count))
                if len(results)%25==0: progress(f'Загружено диалогов: {len(results)}...')
            return results
        finally:
            await disconnect_account_client(client)

    def dialogs_loaded(self,dialogs):
        self.dialog_list.clear()
        for title,peer,unread in dialogs:
            label=f'{title}  ·  {unread} непрочитанных' if unread else title
            item=QListWidgetItem(label); item.setData(Qt.UserRole,peer); item.setToolTip(peer); self.dialog_list.addItem(item)
        self.dialog_status.setText(f'Диалогов загружено: {len(dialogs)}.')
        if dialogs:
            self.dialog_first_load_pending=True
            self.dialog_list.setCurrentRow(0)
        else:
            self.dialog_history.setPlainText('Для этого аккаунта диалоги не найдены.')

    def dialog_selected(self,item,previous=None):
        if item is None: return
        session=self.dialog_account_box.currentData(); peer=item.data(Qt.UserRole)
        if not session or not peer: return
        try: api_id,api_hash=credentials()
        except Exception as error:
            self.dialog_status.setText(f'Ошибка настроек API: {error}')
            return
        self.dialog_title.setText(item.text().split('  ·  ',1)[0])
        self.dialog_history.setPlainText('Загружаю историю сообщений...')
        self.start_dialog_task(lambda progress:self.fetch_dialog_messages(api_id,api_hash,session,peer,progress),'Загружаю сообщения...',self.dialog_messages_loaded)

    async def fetch_dialog_messages(self,api_id,api_hash,session,peer,progress):
        client_options=await prepare_telegram_client()
        client=create_account_client(session,api_id,api_hash,**client_options)
        try:
            progress('Подключаюсь к Telegram и загружаю историю...')
            await connect_account_client(client)
            entity=await telegram_call(client.get_entity(int(peer)),'поиск диалога')
            messages=await telegram_call(client.get_messages(entity,limit=100),'загрузка истории сообщений')
            lines=[]
            for message in reversed(messages):
                date=message.date.astimezone().strftime('%Y-%m-%d %H:%M') if message.date else ''
                sender='Вы' if message.out else 'Собеседник'
                text=message.message or '[медиа- или служебное сообщение]'
                lines.append(f'[{date}] {sender}:\n{text}')
            return '\n\n'.join(lines) or 'В этом диалоге пока нет сообщений.'
        finally:
            await disconnect_account_client(client)

    def dialog_messages_loaded(self,text):
        self.dialog_history.setPlainText(text)
        self.dialog_history.verticalScrollBar().setValue(self.dialog_history.verticalScrollBar().maximum())
        self.dialog_status.setText('История диалога загружена.')

    def send_dialog_message(self):
        item=self.dialog_list.currentItem(); session=self.dialog_account_box.currentData()
        peer=item.data(Qt.UserRole) if item else None
        text=self.dialog_compose.toPlainText().strip()
        if not session or not peer:
            self.dialog_status.setText('Выберите аккаунт и диалог.')
            return
        if not text:
            self.dialog_status.setText('Введите текст сообщения.')
            return
        try: api_id,api_hash=credentials()
        except Exception as error:
            self.dialog_status.setText(f'Ошибка настроек API: {error}')
            return
        self.start_dialog_task(lambda progress:self.send_dialog_message_request(api_id,api_hash,session,peer,text,progress),'Отправляю сообщение...',self.dialog_message_sent)

    async def send_dialog_message_request(self,api_id,api_hash,session,peer,text,progress):
        client_options=await prepare_telegram_client()
        client=create_account_client(session,api_id,api_hash,**client_options)
        try:
            progress('Подключаюсь к Telegram и отправляю сообщение...')
            await connect_account_client(client)
            entity=await telegram_call(client.get_entity(int(peer)),'поиск диалога')
            await telegram_call(client.send_message(entity,text),'отправка сообщения')
            messages=await telegram_call(client.get_messages(entity,limit=100),'обновление истории сообщений')
            lines=[]
            for message in reversed(messages):
                date=message.date.astimezone().strftime('%Y-%m-%d %H:%M') if message.date else ''
                sender='Вы' if message.out else 'Собеседник'
                body=message.message or '[медиа- или служебное сообщение]'
                lines.append(f'[{date}] {sender}:\n{body}')
            return '\n\n'.join(lines) or 'Сообщение отправлено.'
        finally:
            await disconnect_account_client(client)

    def dialog_message_sent(self,text):
        self.dialog_compose.clear(); self.dialog_history.setPlainText(text)
        self.dialog_history.verticalScrollBar().setValue(self.dialog_history.verticalScrollBar().maximum())
        self.dialog_status.setText('Сообщение отправлено.')
    def dialogs_page(self):
        w=QWidget(); layout=QVBoxLayout(w); layout.setContentsMargins(34,30,34,30)
        layout.addWidget(self.heading('Диалоги','Просматривайте переписки и отвечайте от выбранного аккаунта'))
        controls=QHBoxLayout(); self.dialog_account_box=QComboBox(); self.dialog_account_box.setMinimumWidth(260); self.dialog_account_box.currentIndexChanged.connect(self.dialog_account_changed)
        self.dialog_load_button=QPushButton('⟳ Загрузить диалоги'); self.dialog_load_button.clicked.connect(self.load_dialogs)
        controls.addWidget(QLabel('Аккаунт')); controls.addWidget(self.dialog_account_box); controls.addWidget(self.dialog_load_button); controls.addStretch(); layout.addLayout(controls)
        self.dialog_status=QLabel('Выберите аккаунт и загрузите диалоги.'); self.dialog_status.setObjectName('muted'); layout.addWidget(self.dialog_status)
        content=QHBoxLayout(); self.dialog_list=QListWidget(); self.dialog_list.setMinimumWidth(260); self.dialog_list.setMaximumWidth(360); self.dialog_list.currentItemChanged.connect(self.dialog_selected)
        conversation=QVBoxLayout(); self.dialog_title=QLabel('Выберите диалог'); self.dialog_title.setObjectName('title'); conversation.addWidget(self.dialog_title)
        self.dialog_history=QPlainTextEdit(); self.dialog_history.setReadOnly(True); self.dialog_history.setPlaceholderText('История сообщений появится здесь.'); conversation.addWidget(self.dialog_history,1)
        self.dialog_compose=QPlainTextEdit(); self.dialog_compose.setPlaceholderText('Введите сообщение...'); self.dialog_compose.setMaximumHeight(100); conversation.addWidget(self.dialog_compose)
        send_row=QHBoxLayout(); send_row.addStretch(); self.dialog_send_button=QPushButton('➤ Отправить'); self.dialog_send_button.setObjectName('primary'); self.dialog_send_button.clicked.connect(self.send_dialog_message); send_row.addWidget(self.dialog_send_button); conversation.addLayout(send_row)
        content.addWidget(self.dialog_list); content.addLayout(conversation,1); layout.addLayout(content,1); return w
    def refresh(self):
        selected_session=self.account_box.currentData()
        accounts=list(self.conn.execute('SELECT id,name,session,first_name,last_name,username,avatar,account_status FROM accounts ORDER BY name')); chats=list(self.conn.execute('SELECT id,title,peer,assigned_session,chat_type,is_pinned FROM chats ORDER BY is_pinned DESC,title COLLATE NOCASE'))
        self.account_table.setRowCount(0); self.chat_table.setRowCount(0); self.account_box.clear(); self.chat_box.clear(); self.account_unread_badges={}
        for account_id,name,session,first_name,last_name,username,avatar,account_status in accounts:
            r=self.account_table.rowCount(); self.account_table.insertRow(r)
            display_name=' '.join(part for part in (first_name,last_name) if part).strip() or (f'@{username}' if username else name)
            photo=QTableWidgetItem()
            pixmap=QPixmap(avatar) if avatar and Path(avatar).is_file() else QPixmap()
            if not pixmap.isNull():
                photo.setIcon(QIcon(pixmap.scaled(40,40,Qt.KeepAspectRatio,Qt.SmoothTransformation)))
            else: photo.setIcon(self.avatar_placeholder(display_name))
            self.account_table.setItem(r,0,photo)
            self.account_table.setItem(r,1,QTableWidgetItem(display_name))
            self.account_table.setItem(r,2,QTableWidgetItem(f'@{username}' if username else ''))
            self.account_table.setItem(r,3,QTableWidgetItem(Path(session).name))
            status_labels={'deleted':'Аккаунт удалён — больше не действителен','invalid':'Сессия больше не действительна','banned':'Заблокирован Telegram','restricted':'Ограничен Telegram','connection_error':'Ошибка подключения'}
            status_item=QTableWidgetItem(status_labels.get(account_status,'● Профиль загружен' if first_name or username else 'Ожидает подключения'))
            if account_status in status_labels:
                status_item.setForeground(QColor('#ff6b75'))
                status_item.setToolTip(
                    'Telegram пометил этот профиль как удалённый или больше не действительный.' if account_status=='deleted'
                    else 'Telegram отозвал или больше не принимает эту сессию.' if account_status=='invalid'
                    else 'Telegram сообщил о блокировке аккаунта.' if account_status=='banned'
                    else 'Telegram ограничил отправку сообщений с этого аккаунта.' if account_status=='restricted'
                    else 'Не удалось подключиться к Telegram. Нажмите «Обновить профили» и проверьте сообщение об ошибке ниже.'
                )
            self.account_table.setItem(r,4,status_item)
            edit_button=QPushButton('✎ Изменить профиль Telegram')
            edit_button.clicked.connect(lambda _,ident=account_id,account_session=session:self.edit_account_profile(ident,account_session))
            self.account_table.setCellWidget(r,5,edit_button)
            dialogs_button=QPushButton('Диалоги')
            dialogs_button.clicked.connect(lambda _,ident=account_id,account_session=session,account_name=display_name:self.open_account_dialogs(ident,account_session,account_name))
            unread_badge=QLabel('●'); unread_badge.setStyleSheet('color:#ff4d5e;font-size:18px;font-weight:700;'); unread_badge.setAlignment(Qt.AlignCenter)
            unread_count=self.account_unread_counts.get(session,0); unread_badge.setVisible(unread_count>0)
            unread_badge.setToolTip(f'Непрочитанных личных сообщений: {unread_count}' if unread_count else '')
            dialog_cell=QWidget(); dialog_layout=QHBoxLayout(dialog_cell); dialog_layout.setContentsMargins(4,2,4,2); dialog_layout.setSpacing(8)
            dialog_layout.addWidget(dialogs_button,1); dialog_layout.addWidget(unread_badge)
            self.account_table.setCellWidget(r,6,dialog_cell); self.account_unread_badges[session]=unread_badge
            delete_button=QPushButton('Удалить')
            delete_button.clicked.connect(lambda _,ident=account_id,account_session=session,account_name=display_name:self.delete_account(ident,account_session,account_name))
            self.account_table.setCellWidget(r,7,delete_button)
            self.account_table.setRowHeight(r,60)
            label=f'{display_name} (@{username})' if username and display_name != f'@{username}' else display_name
            self.account_box.addItem(label,session)
            if selected_session == session: self.account_box.setCurrentIndex(self.account_box.count()-1)
        for chat_id,title,peer,assigned_session,chat_type,is_pinned in chats:
            r=self.chat_table.rowCount(); self.chat_table.insertRow(r); typ=chat_type or ('ID' if peer.lstrip('-').isdigit() else 'Username/ссылка')
            title_item=QTableWidgetItem(f'📌 {title}' if is_pinned else title)
            title_item.setData(Qt.UserRole,chat_id)
            if is_pinned: title_item.setToolTip('Этот чат закреплён и отображается сверху.')
            self.chat_table.setItem(r,0,title_item)
            self.chat_table.setItem(r,1,QTableWidgetItem(peer))
            self.chat_table.setItem(r,2,QTableWidgetItem(typ))
            account_select=QComboBox(); account_select.addItem('— Выберите аккаунт —','')
            for _,name,session,first_name,last_name,username,_,_ in accounts:
                display_name=' '.join(part for part in (first_name,last_name) if part).strip() or (f'@{username}' if username else name)
                label=f'{display_name} (@{username})' if username else display_name
                account_select.addItem(label,session)
            assigned_index=account_select.findData(assigned_session)
            account_select.setCurrentIndex(assigned_index if assigned_index>=0 else 0)
            account_select.currentIndexChanged.connect(lambda _,ident=chat_id,selector=account_select:self.assign_chat_account(ident,selector.currentData()))
            account_select.installEventFilter(self)
            self.chat_table.setCellWidget(r,3,account_select)
            send_button=QPushButton('➤ Отправить')
            send_button.clicked.connect(lambda _,ident=chat_id,chat_peer=peer,selector=account_select,button=send_button:self.send_default_to_chat(ident,chat_peer,selector.currentData(),button))
            send_button.installEventFilter(self)
            self.chat_table.setCellWidget(r,4,send_button)
            self.chat_table.setRowHeight(r,48)
            self.chat_box.addItem(f'{title}  ·  {peer}',peer)
        self.accounts_metric.findChild(QLabel,'metric').setText(str(len(accounts))); self.chats_metric.findChild(QLabel,'metric').setText(str(len(chats)))

    def eventFilter(self,watched,event):
        if event.type()==QEvent.ContextMenu and self.chat_table is not None:
            position=self.chat_table.viewport().mapFromGlobal(event.globalPos())
            QTimer.singleShot(0,lambda point=position:self.show_chat_context_menu(point))
            return True
        return super().eventFilter(watched,event)

    def show_chat_context_menu(self,position):
        row=self.chat_table.rowAt(position.y())
        if row<0: return
        item=self.chat_table.item(row,0)
        if item is None: return
        chat_id=item.data(Qt.UserRole)
        chat=self.conn.execute('SELECT title,peer,is_pinned FROM chats WHERE id=?',(chat_id,)).fetchone()
        if not chat: return
        title,peer,is_pinned=chat
        menu=QMenu(self)
        pin_action=menu.addAction('Открепить чат' if is_pinned else 'Закрепить чат сверху')
        copy_title_action=menu.addAction('Копировать название')
        copy_peer_action=menu.addAction('Копировать идентификатор')
        menu.addSeparator()
        delete_action=menu.addAction('Удалить чат')
        chosen=menu.exec(self.chat_table.viewport().mapToGlobal(position))
        if chosen==pin_action:
            self.set_chat_pinned(chat_id,not bool(is_pinned))
        elif chosen==copy_title_action:
            QApplication.clipboard().setText(title)
            self.chat_status.setText('Название чата скопировано.')
        elif chosen==copy_peer_action:
            QApplication.clipboard().setText(peer)
            self.chat_status.setText('Идентификатор чата скопирован.')
        elif chosen==delete_action:
            self.delete_chat(chat_id,title)

    def set_chat_pinned(self,chat_id,is_pinned):
        try:
            self.conn.execute('UPDATE chats SET is_pinned=? WHERE id=?',(int(is_pinned),chat_id))
            self.conn.commit()
        except sqlite3.Error as error:
            QMessageBox.critical(self,'Закрепление чата',f'Не удалось сохранить настройку закрепления: {error}')
            return
        self.refresh()
        self.chat_status.setText('Чат закреплён сверху.' if is_pinned else 'Чат откреплён.')

    def delete_chat(self,chat_id,title):
        answer=QMessageBox.question(
            self,
            'Удалить чат из списка',
            f'Удалить «{title}» из списка чатов? Это не удалит чат в Telegram.',
            QMessageBox.Yes|QMessageBox.No,
            QMessageBox.No,
        )
        if answer!=QMessageBox.Yes: return
        try:
            self.conn.execute('DELETE FROM chats WHERE id=?',(chat_id,))
            self.conn.commit()
        except sqlite3.Error as error:
            QMessageBox.critical(self,'Удаление чата',f'Не удалось удалить чат из списка: {error}')
            return
        self.refresh()
        self.chat_status.setText(f'Чат «{title}» удалён из списка программы.')
    def save_default_message(self):
        try:
            settings=json.loads(SETTINGS.read_text(encoding='utf-8')) if SETTINGS.exists() else {}
            settings['default_message']=self.default_message.toPlainText()
            SETTINGS.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
        except (OSError,json.JSONDecodeError) as error:
            QMessageBox.critical(self,'Сообщение по умолчанию',f'Не удалось сохранить data/settings.json: {error}')
            return False
        self.chat_status.setText('Сообщение по умолчанию сохранено. Отправьте его в отдельный чат или запустите авторассылку.')
        return True
    def assign_chat_account(self,chat_id,session):
        try:
            self.conn.execute('UPDATE chats SET assigned_session=? WHERE id=?',(session or '',chat_id))
            self.conn.commit()
        except sqlite3.Error as error:
            self.chat_status.setText(f'Не удалось сохранить аккаунт для чата: {error}')
            QMessageBox.critical(self,'Аккаунт чата',str(error))
    def avatar_placeholder(self,label):
        pixmap=QPixmap(40,40); pixmap.fill(Qt.transparent); painter=QPainter(pixmap); painter.setRenderHint(QPainter.Antialiasing); painter.setBrush(QColor('#263b5c')); painter.setPen(Qt.NoPen); painter.drawEllipse(1,1,38,38); painter.setPen(QColor('#ffffff')); painter.drawText(pixmap.rect(),Qt.AlignCenter,(label[:1] or '?').upper()); painter.end(); return QIcon(pixmap)

    def api_settings(self):
        try:
            settings=json.loads(SETTINGS.read_text(encoding='utf-8')) if SETTINGS.exists() else {}
        except (OSError,json.JSONDecodeError) as error:
            QMessageBox.critical(self,'Настройки',f'Не удалось прочитать data/settings.json: {error}')
            return
        current=(os.environ.get('TG_PROXY') or settings.get('proxy','')).strip()
        try: current_mode,_=configured_proxy_settings()
        except RuntimeError as error:
            QMessageBox.critical(self,'Настройки',str(error))
            return
        dialog=QDialog(self); dialog.setWindowTitle('Подключение Telegram'); form=QFormLayout(dialog)
        api_id_input=QLineEdit(str(settings.get('api_id',os.environ.get('TG_API_ID',''))))
        api_hash_input=QLineEdit(str(settings.get('api_hash',os.environ.get('TG_API_HASH',''))))
        api_hash_input.setEchoMode(QLineEdit.Password)
        form.addRow('API ID',api_id_input)
        form.addRow('API Hash',api_hash_input)
        proxy_mode_box=QComboBox()
        proxy_mode_box.addItem('Без прокси','none')
        proxy_mode_box.addItem('Локальный TgWsProxy','local')
        proxy_mode_box.addItem('Пользовательский прокси','custom')
        mode_index=proxy_mode_box.findData(current_mode)
        proxy_mode_box.setCurrentIndex(mode_index if mode_index>=0 else 0)
        form.addRow('Способ подключения',proxy_mode_box)
        proxy_input=QLineEdit(current)
        proxy_input.setPlaceholderText('IP:порт, IP:порт:логин:пароль или http://host:port')
        form.addRow('Прокси одной строкой',proxy_input)
        proxy_mode_box.currentIndexChanged.connect(lambda _:proxy_input.setEnabled(proxy_mode_box.currentData()=='custom'))
        proxy_input.setEnabled(current_mode=='custom')
        form.addRow(QLabel('Для прямого соединения выберите «Без прокси». Локальный режим требует установленный TgWsProxy. Пользовательский режим поддерживает IP:порт, IP:порт:логин:пароль и proxy URL.'))
        proxy_status=QLabel('Нажмите «Проверить», чтобы проверить выбранный способ подключения.')
        form.addRow(proxy_status)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        test_button=buttons.addButton('Проверить',QDialogButtonBox.ActionRole)
        test_button.clicked.connect(lambda:self.test_proxy_in_dialog(proxy_mode_box,proxy_input,proxy_status))
        if dialog.exec() != QDialog.Accepted: return
        api_id_text=api_id_input.text().strip()
        api_hash=api_hash_input.text().strip()
        try:
            api_id=int(api_id_text)
        except ValueError:
            QMessageBox.warning(self,'Настройки Telegram','API ID должен быть положительным числом.')
            return
        if api_id <= 0 or not api_hash:
            QMessageBox.warning(self,'Настройки Telegram','Введите положительный API ID и API Hash.')
            return
        proxy_mode=proxy_mode_box.currentData()
        try:
            proxy_url=normalize_proxy_url(proxy_input.text()) if proxy_mode=='custom' else ''
            telegram_client_options(proxy_url,proxy_mode)
        except (ValueError,RuntimeError) as error:
            QMessageBox.warning(self,'Настройки Telegram',str(error))
            return
        settings['api_id']=api_id_text
        settings['api_hash']=api_hash
        settings['proxy']=proxy_url
        settings['proxy_mode']=proxy_mode
        try: SETTINGS.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError as error:
            QMessageBox.critical(self,'Настройки',f'Не удалось сохранить data/settings.json: {error}')
            return
        os.environ['TG_API_ID']=api_id_text
        os.environ['TG_API_HASH']=api_hash
        os.environ['TG_PROXY_MODE']=proxy_mode
        if proxy_url: os.environ['TG_PROXY']=proxy_url
        else: os.environ.pop('TG_PROXY',None)
        write_import_log(f'Сохранены настройки подключения: режим {proxy_mode}.')
        self.status.setText('Настройки подключения сохранены: '+('без прокси' if proxy_mode=='none' else 'локальный прокси' if proxy_mode=='local' else 'пользовательский прокси'))
        if self.conn.execute('SELECT 1 FROM accounts LIMIT 1').fetchone(): self.refresh_profiles()

    def test_proxy_in_dialog(self,proxy_mode_box,proxy_input,status):
        if self.proxy_test_thread and self.proxy_test_thread.isRunning():
            status.setText('Проверка уже выполняется. Подождите завершения.')
            status.setStyleSheet('color:#e6b450')
            return
        proxy_mode=proxy_mode_box.currentData()
        try:
            api_id,api_hash=credentials()
            proxy_url=normalize_proxy_url(proxy_input.text()) if proxy_mode=='custom' else ''
            client_options=telegram_client_options(proxy_url,proxy_mode)
        except (RuntimeError,ValueError) as error:
            status.setText(f'Некорректные настройки: {error}')
            status.setStyleSheet('color:#ff7777')
            return
        status.setText('Запускаю локальный прокси и проверяю Telegram...' if proxy_mode=='local' else 'Проверяю прямое соединение с Telegram...' if proxy_mode=='none' else 'Проверяю подключение через указанный прокси...')
        status.setStyleSheet('color:#e6b450')
        thread=QThread(self)
        worker=Worker(lambda:self.check_telegram_connection(api_id,api_hash,client_options,proxy_mode))
        self.proxy_test_thread=thread
        self.proxy_test_worker=worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        success_message='Прямое подключение к Telegram работает.' if proxy_mode=='none' else 'Подключение к Telegram через TgWsProxy работает.' if proxy_mode=='local' else 'Подключение к Telegram через этот прокси работает.'
        worker.done.connect(lambda _:self.proxy_test_finished(status,True,success_message,proxy_mode),Qt.QueuedConnection)
        worker.error.connect(lambda error:self.proxy_test_finished(status,False,error,proxy_mode),Qt.QueuedConnection)
        worker.done.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda:self.proxy_test_thread_stopped(thread))
        thread.start()

    async def check_telegram_connection(self,api_id,api_hash,client_options,proxy_mode):
        from telethon.sessions import StringSession
        if proxy_mode=='local':
            try:
                await asyncio.wait_for(asyncio.to_thread(ensure_tgws_proxy),timeout=12)
            except asyncio.TimeoutError as error:
                raise TimeoutError('Локальный TgWsProxy не запустился за 12 секунд.') from error
        client=TelegramClient(StringSession(),api_id,api_hash,**client_options,timeout=5,connection_retries=0,retry_delay=1)
        try:
            try:
                await asyncio.wait_for(client.connect(),timeout=15)
            except asyncio.TimeoutError as error:
                raise TimeoutError('Выбранный способ подключения не завершил соединение с Telegram за 15 секунд.') from error
            if not client.is_connected():
                raise ConnectionError('Telegram не подтвердил подключение.')
            return True
        finally:
            try:
                await asyncio.wait_for(client.disconnect(),timeout=2)
            except asyncio.TimeoutError:
                pass

    def proxy_test_finished(self,status,success,message,proxy_mode):
        if success:
            status.setText(message)
            status.setStyleSheet('color:#6fce8b')
        else:
            if 'Connection to Telegram failed' in message:
                message='Не удалось подключиться к Telegram. Проверьте Интернет.' if proxy_mode=='none' else 'Не удалось подключиться к Telegram. Проверьте наличие локального прокси и его конфигурацию.' if proxy_mode=='local' else 'Не удалось подключиться к Telegram. Проверьте адрес, порт, логин и пароль прокси.'
            status.setText(f'Ошибка подключения: {message}')
            status.setStyleSheet('color:#ff7777')

    def proxy_test_thread_stopped(self,thread):
        if self.proxy_test_thread is thread: self.proxy_test_thread=None
        thread.deleteLater()
    def import_zip(self):
        try:
            source,_=QFileDialog.getOpenFileName(self,'Выберите ZIP с TDATA',str(Path.home()),'ZIP-архивы (*.zip)')
            write_import_log(f'ZIP-диалог вернул путь: {source!r}')
            if not source:
                self.import_status.setText('ZIP не выбран — импорт не запускался')
                return
            self.process_tdata(source)
        except Exception as e:
            write_import_log(f'Ошибка диалога ZIP: {type(e).__name__}: {e}')
            QMessageBox.critical(self,'Импорт ZIP',f'{type(e).__name__}: {e}')

    def import_folder(self):
        try:
            source=QFileDialog.getExistingDirectory(self,'Выберите папку tdata',str(Path.home()))
            write_import_log(f'Диалог папки вернул путь: {source!r}')
            if not source:
                self.import_status.setText('Папка не выбрана — импорт не запускался')
                return
            self.process_tdata(source)
        except Exception as e:
            write_import_log(f'Ошибка диалога папки: {type(e).__name__}: {e}')
            QMessageBox.critical(self,'Импорт папки TDATA',f'{type(e).__name__}: {e}')

    def process_tdata(self,source):
        if getattr(self,'import_thread',None) and self.import_thread.isRunning():
            QMessageBox.information(self,'Импорт','Подождите, пока завершится текущий импорт.')
            return
        write_import_log(f'Выбран источник: {source}\nИмпорт запущен в фоновом потоке.')
        self.import_status.setText('Подготовка архива и проверка TDATA...')
        self.import_thread=QThread(self); self.import_worker=ImportWorker(lambda:self.convert_tdata(source))
        self.import_worker.moveToThread(self.import_thread)
        self.import_thread.started.connect(self.import_worker.run)
        self.import_worker.done.connect(self.import_succeeded)
        self.import_worker.error.connect(self.import_failed)
        self.import_worker.done.connect(self.import_thread.quit)
        self.import_worker.error.connect(self.import_thread.quit)
        self.import_thread.finished.connect(self.import_worker.deleteLater)
        self.import_thread.finished.connect(lambda finished=self.import_thread:self.import_thread_finished(finished))
        self.import_thread.start()

    def convert_tdata(self,source):
        temp=None
        try:
            if unsupported_tdata_python():
                raise RuntimeError('Импорт TDATA требует Python 3.11–3.13. Запустите install.bat, чтобы создать совместимое окружение.')
            root=Path(source)
            if root.is_file() and root.suffix.lower()=='.zip':
                write_import_log(f'Распаковка ZIP: {source}')
                temp=Path(tempfile.mkdtemp(prefix='tgacc_'))
                with zipfile.ZipFile(root) as archive:
                    base=temp.resolve()
                    for item in archive.infolist():
                        target=(temp/item.filename).resolve()
                        if not str(target).startswith(str(base)+os.sep): raise RuntimeError('ZIP содержит небезопасный путь')
                    archive.extractall(temp)
                root=temp
            candidates=[root]+[p for p in root.rglob('*') if p.is_dir() and p.name.lower()=='tdata']
            source_dir=next((candidate for candidate in candidates if any(p.is_file() and p.name.lower()=='key_datas' for p in candidate.iterdir())),None)
            if source_dir is None: raise RuntimeError('Папка TDATA не распознана: не найден файл key_datas.')
            all_files=[p for p in source_dir.rglob('*') if p.is_file()]
            key_file=next((p for p in all_files if p.name.lower()=='key_datas'),None)
            map_files=[p for p in all_files if p.name.lower().startswith('map')]
            top_items=list(source_dir.iterdir())
            write_import_log(
                f'TDATA найдена: {source_dir}\n'
                f'Файлов: {len(all_files)}, каталогов: {sum(p.is_dir() for p in source_dir.rglob("*"))}; '
                f'key_datas: {key_file.stat().st_size if key_file else 0} байт; map*: {len(map_files)}\n'
                f'Верхний уровень: {[p.name for p in top_items[:30]]}\n'
                + ('ПРЕДУПРЕЖДЕНИЕ: map* не найдены; архив может быть неполным или это не TDATA.\n' if not map_files else '')
                + 'Начинаю загрузку opentele.'
            )
            from opentele.api import UseCurrentSession
            from opentele.td import TDesktop
            desktop=TDesktop(str(source_dir))
            if not desktop.isLoaded(): raise RuntimeError('TDATA найдена, но не загрузилась. Проверьте, что это полный профиль Telegram Desktop.')
            name=source_dir.parent.name if source_dir.name.lower()=='tdata' else source_dir.name
            safe=re.sub(r'[^a-zA-Z0-9_-]','_',name) or 'account'; path=SESSIONS/f'{safe}.session'
            write_import_log(f'TDATA загружена; конвертирую в Telethon session: {path}')
            client=asyncio.run(desktop.ToTelethon(str(path),flag=UseCurrentSession))
            try:
                client.session.save()
            finally:
                client.session.close()
            if not Path(path).exists(): raise RuntimeError('opentele не создал Telethon session-файл.')
            return name,str(path),str(source_dir),str(temp) if temp else ''
        finally:
            if temp: shutil.rmtree(temp,ignore_errors=True)

    def import_succeeded(self,result):
        name,path,source_dir,_=result
        self.conn.execute('INSERT INTO accounts(name,session) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET session=excluded.session',(name,path)); self.conn.commit(); self.refresh()
        self.import_status.setText(f'Импорт успешно завершён: {name}')
        self.status.setText(f'Импортирован аккаунт: {name}')
        write_import_log(f'Импорт успешно завершён: {name}\nTDATA: {source_dir}\nСессия: {path}')
        self.refresh_profiles()

    def refresh_profiles(self,show_errors=True):
        if self.profile_thread and self.profile_thread.isRunning():
            self.import_status.setText('Обновление профилей уже выполняется. Дождитесь результата ниже.')
            return
        accounts=list(self.conn.execute('SELECT id,session FROM accounts ORDER BY id'))
        if not accounts:
            self.import_status.setText('Сначала импортируйте профиль TDATA.')
            return
        try: api_id,api_hash=credentials(); client_options=telegram_client_options()
        except Exception as error:
            self.import_status.setText(f'Не удалось подключиться для загрузки профилей: {error}')
            if show_errors: QMessageBox.warning(self,'Профили Telegram',str(error))
            return
        self.import_status.setText('Подключаюсь к Telegram и проверяю имя, username и статус аккаунтов...')
        self.profile_thread=QThread(self)
        self.profile_worker=Worker(lambda progress,item:self.load_profiles(accounts,api_id,api_hash,client_options,progress),with_progress=True)
        self.profile_worker.moveToThread(self.profile_thread)
        self.profile_thread.started.connect(self.profile_worker.run)
        self.profile_worker.progress.connect(self.import_status.setText)
        self.profile_worker.done.connect(lambda results,display_errors=show_errors:self.profiles_loaded(results,display_errors),Qt.QueuedConnection)
        self.profile_worker.error.connect(lambda error,display_errors=show_errors:self.profile_load_failed(error,display_errors),Qt.QueuedConnection)
        self.profile_worker.done.connect(self.profile_thread.quit,Qt.DirectConnection)
        self.profile_worker.error.connect(self.profile_thread.quit,Qt.DirectConnection)
        self.profile_thread.finished.connect(self.profile_worker.deleteLater)
        self.profile_thread.finished.connect(lambda thread=self.profile_thread:self.profile_thread_finished(thread))
        self.profile_thread.start()

    def profile_thread_finished(self,thread):
        if self.profile_thread is thread: self.profile_thread=None
        thread.deleteLater()

    def import_thread_finished(self,thread):
        if self.import_thread is thread: self.import_thread=None
        thread.deleteLater()

    async def load_profiles(self,accounts,api_id,api_hash,client_options,progress=None):
        results=[]
        if configured_proxy_settings()[0]=='local': await asyncio.to_thread(ensure_tgws_proxy)
        for index,(account_id,session_path) in enumerate(accounts,1):
            if progress: progress(f'Проверяю аккаунт {index}/{len(accounts)}: подключаюсь к Telegram...')
            client=None
            result=None
            try:
                client=create_account_client(session_path,api_id,api_hash,auto_reconnect=False,**client_options)
                try:
                    if progress: progress(f'Проверяю аккаунт {index}/{len(accounts)}: подключаюсь и проверяю авторизацию...')
                    await connect_account_client(client,timeout=15)
                    if progress: progress(f'Проверяю аккаунт {index}/{len(accounts)}: получаю данные профиля...')
                    user=await asyncio.wait_for(client.get_me(),timeout=15)
                except asyncio.TimeoutError as error:
                    raise TimeoutError('Telegram не ответил за 15 секунд. Проверьте подключение к Интернету и способ прокси.') from error
                if user is None:
                    result=(account_id,None,'','','','','','invalid','')
                elif getattr(user,'deleted',False):
                    result=(account_id,user.id,'','','','','','deleted','')
                else:
                    first_name=user.first_name or ''
                    last_name=user.last_name or ''
                    username=user.username or ''
                    if progress: progress(f'Аккаунт {index}/{len(accounts)} подтверждён; обновляю имя и username...')
                    result=(account_id,user.id,first_name,last_name,username,None,None,'','')
            except Exception as error:
                if account_issue_from_error(error)=='deleted':
                    result=(account_id,None,'','','','','','deleted','')
                elif account_issue_from_error(error)=='invalid':
                    result=(account_id,None,'','','','','','invalid','')
                elif 'Сессия аккаунта не авторизована' in str(error):
                    result=(account_id,None,'','','','','','invalid','')
                else:
                    result=(account_id,None,'','','','','','',f'{type(error).__name__}: {error}')
            finally:
                if client is not None:
                    try:
                        await asyncio.wait_for(client.disconnect(),timeout=3)
                    except Exception as error:
                        if result is None:
                            result=(account_id,None,'','','','','','',f'Не удалось закрыть соединение с Telegram: {type(error).__name__}: {error}')
            results.append(result)
        return results

    def profiles_loaded(self,results,show_errors=True):
        errors=[]
        deleted_accounts=[]
        invalid_accounts=[]
        for account_id,telegram_id,first_name,last_name,username,avatar,about,account_status,error in results:
            if error:
                session_row=self.conn.execute('SELECT session FROM accounts WHERE id=?',(account_id,)).fetchone()
                account_row=self.conn.execute('SELECT name FROM accounts WHERE id=?',(account_id,)).fetchone()
                account_name=account_row[0] if account_row else f'ID {account_id}'
                errors.append(f'{account_name}: {error}')
                if session_row and not self.mark_account_issue(session_row[0],error,refresh=False):
                    self.conn.execute('UPDATE accounts SET account_status=? WHERE id=? AND account_status NOT IN (\'deleted\',\'invalid\',\'banned\',\'restricted\')',('connection_error',account_id))
                continue
            if account_status in ('deleted','invalid'):
                self.conn.execute('UPDATE accounts SET telegram_id=?,account_status=? WHERE id=?',(telegram_id,account_status,account_id))
                account_row=self.conn.execute('SELECT name FROM accounts WHERE id=?',(account_id,)).fetchone()
                if account_row:
                    (deleted_accounts if account_status=='deleted' else invalid_accounts).append(account_row[0])
                continue
            self.conn.execute('UPDATE accounts SET telegram_id=?,first_name=?,last_name=?,username=?,avatar=COALESCE(?,avatar),about=COALESCE(?,about),account_status=\'\' WHERE id=?',(telegram_id,first_name,last_name,username,avatar,about,account_id))
        self.conn.commit()
        self.refresh()
        if errors:
            error_text='; '.join(errors[:2])
            if 'Connection to Telegram failed' in error_text:
                error_text+=' Проверьте доступ к Telegram или укажите прокси в «Настройках API».'
            self.import_status.setText(f'Не удалось загрузить профиль: {error_text}')
            write_import_log(f'Не удалось загрузить данные профиля: {error_text}')
            if show_errors: QMessageBox.warning(self,'Профили Telegram',error_text)
        else:
            if deleted_accounts or invalid_accounts:
                status_messages=[]
                if deleted_accounts: status_messages.append(f'Удалённые или недействительные: {", ".join(deleted_accounts)}')
                if invalid_accounts: status_messages.append(f'Сессия больше недействительна: {", ".join(invalid_accounts)}')
                self.import_status.setText('; '.join(status_messages)+'.')
                write_import_log('Результат проверки аккаунтов: '+'; '.join(status_messages))
            else:
                self.import_status.setText('Имена и username обновлены; сохранённые фото и описание не менялись.')
                write_import_log(f'Имена и username профилей Telegram обновлены: {len(results)}')
        self.status.setText(self.import_status.text())

    def mark_account_issue(self,session,error,refresh=True):
        issue=account_issue_from_error(error)
        if not issue:
            return False
        row=self.conn.execute('SELECT id,account_status FROM accounts WHERE session=?',(session,)).fetchone()
        if not row:
            return False
        account_id,current_status=row
        if current_status=='deleted' or (current_status=='banned' and issue=='restricted'):
            return False
        self.conn.execute('UPDATE accounts SET account_status=? WHERE id=?',(issue,account_id))
        self.conn.commit()
        write_import_log(f'Аккаунт {Path(session).name} получил статус {issue} по ответу Telegram: {error}')
        if refresh:
            self.refresh()
        messages={
            'deleted':'Telegram сообщил, что аккаунт удалён.',
            'invalid':'Telegram больше не принимает эту сессию аккаунта.',
            'banned':'Telegram сообщил, что аккаунт заблокирован.',
            'restricted':'Telegram ограничил отправку сообщений с этого аккаунта.',
        }
        self.import_status.setText(messages[issue])
        return True

    def delete_account(self,account_id,session,name):
        active_threads=('import_thread','profile_thread','profile_edit_thread','chatlist_thread','chat_send_thread','thread','unread_poll_thread')
        if any((thread:=getattr(self,attribute,None)) is not None and thread.isRunning() for attribute in active_threads):
            QMessageBox.information(self,'Удаление аккаунта','Дождитесь завершения текущей операции Telegram и попробуйте ещё раз.')
            return
        if session in self.dialog_windows:
            QMessageBox.information(self,'Удаление аккаунта','Сначала закройте окно диалогов этого аккаунта.')
            return
        answer=QMessageBox.question(
            self,
            'Удалить аккаунт',
            f'Удалить «{name}» из программы и удалить его локальный файл сессии? '
            'Список чатов останется, но назначение этого аккаунта будет очищено.',
            QMessageBox.Yes|QMessageBox.No,
            QMessageBox.No,
        )
        if answer!=QMessageBox.Yes:
            return
        row=self.conn.execute('SELECT avatar FROM accounts WHERE id=? AND session=?',(account_id,session)).fetchone()
        if not row:
            QMessageBox.warning(self,'Аккаунт не найден','Обновите список аккаунтов и попробуйте снова.')
            return
        avatar=row[0]
        try:
            with self.conn:
                self.conn.execute('DELETE FROM accounts WHERE id=? AND session=?',(account_id,session))
                if not self.conn.execute('SELECT 1 FROM accounts WHERE session=?',(session,)).fetchone():
                    self.conn.execute('UPDATE chats SET assigned_session=\'\' WHERE assigned_session=?',(session,))
        except sqlite3.Error as error:
            QMessageBox.critical(self,'Удаление аккаунта',f'Не удалось удалить аккаунт из базы данных: {error}')
            return
        self.account_unread_counts.pop(session,None)
        self.refresh()
        cleanup_errors=[]
        session_is_shared=self.conn.execute('SELECT 1 FROM accounts WHERE session=?',(session,)).fetchone()
        paths=[]
        if not session_is_shared:
            paths.append((Path(session),SESSIONS))
        if avatar and not self.conn.execute('SELECT 1 FROM accounts WHERE avatar=?',(avatar,)).fetchone():
            paths.append((Path(avatar),AVATARS))
        for path,managed_root in paths:
            try:
                if not path.exists():
                    continue
                resolved=path.resolve()
                if not resolved.is_relative_to(managed_root.resolve()):
                    cleanup_errors.append(f'Файл оставлен вне каталога приложения: {path}')
                    continue
                candidates=(path,Path(str(path)+'-journal'),Path(str(path)+'-wal'),Path(str(path)+'-shm'))
                for candidate in candidates:
                    if candidate.exists():
                        candidate.unlink()
            except OSError as error:
                cleanup_errors.append(f'{path}: {error}')
        self.import_status.setText(f'Аккаунт «{name}» удалён из программы.')
        if cleanup_errors:
            QMessageBox.warning(self,'Аккаунт удалён',f'Аккаунт удалён из программы, но некоторые файлы не удалось удалить:\n'+'\n'.join(cleanup_errors))
        else:
            QMessageBox.information(self,'Аккаунт удалён',f'Аккаунт «{name}» и его локальные файлы удалены.')

    def profile_load_failed(self,error,show_dialog=True):
        self.import_status.setText(f'Не удалось загрузить профили: {error}')
        write_import_log(f'Ошибка загрузки профилей: {error}')
        if show_dialog: QMessageBox.warning(self,'Профили Telegram',error)

    def edit_account_profile(self,account_id,session):
        if any(thread and thread.isRunning() for thread in (self.profile_edit_thread,self.profile_thread)):
            self.import_status.setText('Дождитесь завершения текущего изменения или обновления профиля.')
            return
        row=self.conn.execute('SELECT name,first_name,last_name,avatar,about,username FROM accounts WHERE id=? AND session=?',(account_id,session)).fetchone()
        if not row:
            QMessageBox.warning(self,'Аккаунт не найден','Не удалось найти выбранный аккаунт. Обновите список аккаунтов.')
            return
        name,first_name,last_name,avatar,about,username=row
        self.show_profile_editor(account_id,session,name,first_name,last_name,avatar,about,username)

    def open_account_dialogs(self,account_id,session,name):
        if self.profile_edit_thread and self.profile_edit_thread.isRunning():
            self.import_status.setText('Дождитесь завершения изменения профиля перед открытием диалогов.')
            return
        existing=self.dialog_windows.get(session)
        if existing is not None:
            existing.show(); existing.raise_(); existing.activateWindow()
            return
        try: api_id,api_hash=credentials()
        except Exception as error:
            QMessageBox.warning(self,'Настройки API',str(error))
            return
        dialog=TelegramDialogsWindow(name,session,api_id,api_hash,self)
        self.dialog_windows[session]=dialog
        dialog.privateUnreadChanged.connect(self.account_private_unread_changed)
        dialog.worker.failed.connect(lambda error,account_session=session:self.mark_account_issue(account_session,error))
        dialog.worker.connected.connect(self.account_dialog_connected)
        dialog.destroyed.connect(lambda _,account_session=session:self.account_dialog_closed(account_session))
        dialog.show()

    def account_dialog_connected(self,session):
        self.dialog_connected_sessions.add(session)

    def account_dialog_closed(self,session):
        self.dialog_windows.pop(session,None)
        self.dialog_connected_sessions.discard(session)

    def account_private_unread_changed(self,session,count):
        self.account_unread_counts[session]=count
        badge=self.account_unread_badges.get(session)
        if badge is not None:
            badge.setVisible(count>0)
            badge.setToolTip(f'Непрочитанных личных сообщений: {count}' if count else '')

    def refresh_private_unread(self):
        if self.unread_poll_thread and self.unread_poll_thread.isRunning(): return
        if any(thread and thread.isRunning() for thread in (self.profile_thread,self.profile_edit_thread,self.chatlist_thread,self.chat_send_thread,self.thread)):
            return
        accounts=list(self.conn.execute('SELECT session FROM accounts ORDER BY name'))
        active_sessions=set(self.dialog_connected_sessions)
        accounts=[session for (session,) in accounts if session not in active_sessions]
        if not accounts: return
        try: api_id,api_hash=credentials()
        except Exception as error:
            write_import_log(f'Не удалось проверить непрочитанные личные сообщения: {error}')
            return
        thread=QThread(self); worker=Worker(lambda:self.fetch_private_unread_counts(accounts,api_id,api_hash))
        self.unread_poll_thread=thread; self.unread_poll_worker=worker
        worker.moveToThread(thread); thread.started.connect(worker.run)
        worker.done.connect(self.private_unread_counts_loaded); worker.error.connect(self.private_unread_poll_failed)
        worker.done.connect(thread.quit); worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater); thread.finished.connect(lambda finished=thread:self.private_unread_poll_finished(finished))
        thread.start()

    async def fetch_private_unread_counts(self,sessions,api_id,api_hash):
        from telethon.tl.types import User
        results={}; account_issues=[]
        options=await prepare_telegram_client()
        for session in sessions:
            client=create_account_client(session,api_id,api_hash,**options)
            try:
                await connect_account_client(client)
                unread=0
                async for dialog in client.iter_dialogs(limit=100):
                    if isinstance(dialog.entity,User) and not dialog.entity.bot:
                        unread+=dialog.unread_count
                results[session]=unread
            except Exception as error:
                write_import_log(f'Не удалось проверить личные сообщения аккаунта {Path(session).name}: {type(error).__name__}: {error}')
                if account_issue_from_error(error): account_issues.append((session,f'{type(error).__name__}: {error}'))
            finally:
                await client.disconnect()
        return results,account_issues

    def private_unread_counts_loaded(self,result):
        results,account_issues=result
        for session,count in results.items():
            self.account_private_unread_changed(session,count)
        for session,error in account_issues:
            self.mark_account_issue(session,error,refresh=False)
        if account_issues: self.refresh()

    def private_unread_poll_failed(self,error):
        write_import_log(f'Ошибка проверки непрочитанных личных сообщений: {error}')

    def private_unread_poll_finished(self,thread):
        if self.unread_poll_thread is thread: self.unread_poll_thread=None
        thread.deleteLater()

    def show_profile_editor(self,account_id,session,name,current_first_name,current_last_name,current_avatar,current_about,current_username=''):
        dialog=QDialog(self); dialog.setWindowTitle(f'Изменить профиль Telegram — {name}'); dialog.resize(540,600)
        layout=QVBoxLayout(dialog); form=QFormLayout()
        first_name=QLineEdit(current_first_name)
        first_name.setPlaceholderText('Имя, которое увидят в Telegram')
        last_name=QLineEdit(current_last_name)
        last_name.setPlaceholderText('Фамилия (необязательно)')
        form.addRow('Имя в Telegram',first_name)
        form.addRow('Фамилия в Telegram',last_name)
        current_photo=QLabel()
        current_photo.setFixedSize(112,112); current_photo.setAlignment(Qt.AlignCenter); current_photo.setObjectName('panel')
        self.set_profile_preview(current_photo,current_avatar,'Нет текущей аватарки')
        form.addRow('Текущая аватарка',current_photo)
        photo_row=QWidget(); photo_layout=QHBoxLayout(photo_row); photo_layout.setContentsMargins(0,0,0,0)
        photo_path=QLineEdit(); photo_path.setReadOnly(True); photo_path.setPlaceholderText('Не выбрано — аватар не изменится')
        new_photo_preview=QLabel('Новое фото не выбрано'); new_photo_preview.setFixedSize(72,72); new_photo_preview.setAlignment(Qt.AlignCenter); new_photo_preview.setObjectName('panel')
        photo_button=QPushButton('Выбрать фото')
        photo_button.clicked.connect(lambda:self.choose_profile_photo(photo_path,new_photo_preview))
        photo_layout.addWidget(photo_path); photo_layout.addWidget(photo_button); photo_layout.addWidget(new_photo_preview)
        form.addRow('Новая аватарка',photo_row)
        username=QLineEdit(current_username); username.setObjectName('profileUsername'); username.setPlaceholderText('например, my_username')
        username_hint=QLabel('Username должен быть уникальным и содержать от 5 до 32 латинских букв, цифр или подчёркиваний.')
        username_hint.setObjectName('muted'); username_hint.setWordWrap(True)
        username_row=QWidget(); username_layout=QVBoxLayout(username_row); username_layout.setContentsMargins(0,0,0,0); username_layout.addWidget(username); username_layout.addWidget(username_hint)
        form.addRow('Username (@)',username_row)
        clear_username=QCheckBox('Удалить username')
        clear_username.toggled.connect(lambda checked:username.setEnabled(not checked)); form.addRow('',clear_username)
        about=QPlainTextEdit(); about.setPlainText(current_about); about.setPlaceholderText('Описание «О себе» (до 70 символов)')
        about.setMaximumHeight(100); form.addRow('Текущее описание',about)
        clear_about=QCheckBox('Очистить описание')
        clear_about.toggled.connect(lambda checked:about.setEnabled(not checked)); form.addRow('',clear_about)
        layout.addLayout(form)
        note=QLabel('Имя, фамилия, username, описание и аватарка изменяются в самом Telegram.')
        note.setObjectName('muted'); layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec()!=QDialog.Accepted: return
        selected_photo=photo_path.text().strip()
        first_name_text=first_name.text().strip()
        last_name_text=last_name.text().strip()
        username_text='' if clear_username.isChecked() else username.text().strip().removeprefix('@')
        about_text='' if clear_about.isChecked() else about.toPlainText().strip()
        should_update_name=first_name_text!=current_first_name or last_name_text!=current_last_name
        should_update_username=(clear_username.isChecked() and bool(current_username)) or username_text!=current_username
        should_update_about=clear_about.isChecked() or about_text!=current_about
        if not selected_photo and not should_update_name and not should_update_about and not should_update_username: return
        if should_update_name and not first_name_text:
            QMessageBox.warning(self,'Имя Telegram','Имя в Telegram не может быть пустым.')
            return
        if should_update_username and username_text and (not 5<=len(username_text)<=32 or not re.fullmatch(r'[A-Za-z0-9_]+',username_text)):
            QMessageBox.warning(self,'Неверный username','Введите от 5 до 32 символов: латинские буквы, цифры и подчёркивания. Символ @ добавлять не нужно.')
            return
        if should_update_about and len(about_text)>70:
            QMessageBox.warning(self,'Слишком длинное описание','Описание Telegram должно содержать не более 70 символов.')
            return
        if selected_photo and not Path(selected_photo).is_file():
            QMessageBox.warning(self,'Фото не найдено','Выбранный файл аватарки не существует. Выберите изображение ещё раз.')
            return
        try: api_id,api_hash=credentials()
        except Exception as error:
            QMessageBox.warning(self,'Настройки API',str(error))
            return
        self.import_status.setText('Обновляю профиль в Telegram...')
        thread=QThread(self)
        worker=Worker(lambda:self.update_account_profile(account_id,session,selected_photo,first_name_text,last_name_text,should_update_name,username_text,should_update_username,about_text,should_update_about,api_id,api_hash))
        self.profile_edit_thread=thread; self.profile_edit_worker=worker
        worker.moveToThread(thread); thread.started.connect(worker.run)
        worker.done.connect(self.profile_edit_succeeded); worker.error.connect(lambda error:self.profile_edit_failed(error,session),Qt.QueuedConnection)
        worker.done.connect(thread.quit,Qt.DirectConnection); worker.error.connect(thread.quit,Qt.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda finished=thread:self.profile_edit_thread_finished(finished))
        thread.start()

    def set_profile_preview(self,label,path,empty_text):
        pixmap=QPixmap(path) if path and Path(path).is_file() else QPixmap()
        if pixmap.isNull():
            label.setPixmap(QPixmap()); label.setText(empty_text)
            return
        label.setText('')
        label.setPixmap(pixmap.scaled(label.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))

    def choose_profile_photo(self,line_edit,preview):
        path,_=QFileDialog.getOpenFileName(self,'Выберите новую аватарку',str(Path.home()),'Изображения (*.jpg *.jpeg *.png *.webp *.bmp)')
        if path:
            line_edit.setText(path)
            self.set_profile_preview(preview,path,'')

    async def update_account_profile(self,account_id,session,photo_path,first_name,last_name,should_update_name,username,should_update_username,about,should_update_about,api_id,api_hash):
        from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest
        from telethon.tl.functions.photos import UploadProfilePhotoRequest
        client_options=await prepare_telegram_client()
        client=create_account_client(session,api_id,api_hash,**client_options)
        updated=[]
        try:
            write_import_log(f'Начато изменение профиля аккаунта {Path(session).name}.')
            await connect_account_client(client)
            try:
                if photo_path:
                    uploaded=await telegram_call(client.upload_file(photo_path),'загрузка новой аватарки',timeout=60)
                    await telegram_call(client(UploadProfilePhotoRequest(file=uploaded)),'обновление аватарки')
                    updated.append('аватарка')
                if should_update_name or should_update_about:
                    await telegram_call(client(UpdateProfileRequest(
                        first_name=first_name if should_update_name else None,
                        last_name=last_name if should_update_name else None,
                        about=about if should_update_about else None,
                    )),'обновление имени и описания')
                if should_update_name:
                    updated.append('имя профиля Telegram')
                if should_update_about:
                    updated.append('описание')
                if should_update_username:
                    await telegram_call(client(UpdateUsernameRequest(username=username)),'обновление username')
                    updated.append('username')
            except Exception as error:
                from telethon.errors import UsernameInvalidError, UsernameOccupiedError
                if isinstance(error,UsernameOccupiedError):
                    error_message='Этот username уже занят. Попробуйте другой.'
                elif isinstance(error,UsernameInvalidError):
                    error_message='Telegram отклонил username. Проверьте его формат и допустимую длину.'
                else:
                    error_message=''
                if updated:
                    completed=', '.join(updated)
                    detail=error_message or f'Ошибка Telegram: {type(error).__name__}: {error}'
                    raise RuntimeError(f'Частичное обновление: {completed} обновлено, остальные изменения не применены. {detail}') from error
                if error_message: raise RuntimeError(error_message) from error
                raise
            user=await telegram_call(client.get_me(),'получение обновлённого профиля')
            if user is None: raise RuntimeError('Telegram не вернул данные обновлённого профиля.')
            first_name,last_name,username,avatar,updated_about=await telegram_profile_snapshot(client,user)
            write_import_log(f'Профиль аккаунта {Path(session).name} обновлён: {", ".join(updated) or "данные профиля"}.')
            return account_id,user.id,first_name,last_name,username,avatar,updated_about,updated
        finally:
            await disconnect_account_client(client)

    def profile_edit_succeeded(self,result):
        account_id,telegram_id,first_name,last_name,username,avatar,about,updated=result
        self.conn.execute('UPDATE accounts SET telegram_id=?,first_name=?,last_name=?,username=?,avatar=?,about=? WHERE id=?',(telegram_id,first_name,last_name,username,avatar,about,account_id))
        self.conn.commit()
        self.refresh()
        self.import_status.setText(f'Обновлено в Telegram: {", ".join(updated)}.')
        self.status.setText(self.import_status.text())
        QMessageBox.information(self,'Профиль Telegram',self.import_status.text())

    def profile_edit_failed(self,error,session=''):
        error=visible_error_message(error)
        self.mark_account_issue(session,error)
        self.import_status.setText(f'Не удалось изменить профиль: {error}')
        self.status.setText('Ошибка изменения профиля')
        QMessageBox.critical(self,'Ошибка изменения профиля',str(error))

    def profile_edit_thread_finished(self,thread):
        if self.profile_edit_thread is thread: self.profile_edit_thread=None
        thread.deleteLater()

    def import_failed(self,error):
        write_import_log(f'Ошибка импорта: {error}')
        self.import_status.setText(f'Ошибка импорта: {error}')
        QMessageBox.critical(self,'Ошибка импорта TDATA',error)
    def import_chat_folder(self):
        if self.chatlist_thread and self.chatlist_thread.isRunning():
            self.chat_status.setText('Импорт папки чатов уже выполняется.')
            return
        accounts=list(self.conn.execute('SELECT id,name,session,first_name,last_name,username FROM accounts ORDER BY name'))
        if not accounts:
            QMessageBox.warning(self,'Нет аккаунтов','Сначала импортируйте аккаунт Telegram.')
            return
        dialog=QDialog(self); dialog.setWindowTitle('Импорт папки чатов Telegram'); dialog.resize(560,220)
        layout=QVBoxLayout(dialog); form=QFormLayout()
        link=QLineEdit(); link.setPlaceholderText('https://t.me/addlist/...')
        form.addRow('Ссылка на папку',link)
        account_box=QComboBox()
        for _,name,session,first_name,last_name,username in accounts:
            display_name=' '.join(part for part in (first_name,last_name) if part).strip() or (f'@{username}' if username else name)
            account_box.addItem(display_name,session)
        form.addRow('Аккаунт Telegram',account_box)
        layout.addLayout(form)
        warning=QLabel('Список доступных чатов будет добавлен в программу без вступления аккаунта в папку или чаты. При ручной отправке аккаунт сможет вступить в публичную супергруппу; для приватных чатов вступите в Telegram вручную.')
        warning.setWordWrap(True); warning.setObjectName('muted'); layout.addWidget(warning)
        buttons=QDialogButtonBox(QDialogButtonBox.Cancel)
        import_button=buttons.addButton('Импортировать чаты',QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        import_button.clicked.connect(dialog.accept); layout.addWidget(buttons)
        if dialog.exec()!=QDialog.Accepted: return
        match=re.fullmatch(r'(?:https?://)?(?:www\.)?t\.me/addlist/([A-Za-z0-9_-]+)(?:[?#].*)?',link.text().strip(),flags=re.I)
        if not match:
            QMessageBox.warning(self,'Неверная ссылка','Укажите ссылку вида https://t.me/addlist/...')
            return
        try: api_id,api_hash=credentials()
        except Exception as error:
            QMessageBox.warning(self,'Настройки API',str(error))
            return
        self.chat_status.setText('Подготовка к импорту папки чатов...')
        self.chat_folder_button.setEnabled(False); self.chat_progress.show()
        session=account_box.currentData(); slug=match.group(1)
        self.chat_folder_save_error=None; self.chat_folder_imported_count=0
        self.chat_folder_import_session=session
        thread=QThread(self); worker=Worker(lambda progress,item:self.load_chat_folder(api_id,api_hash,session,slug,progress,lambda chat:item((session,chat))),with_progress=True)
        self.chatlist_thread=thread; self.chatlist_worker=worker
        worker.moveToThread(thread); thread.started.connect(worker.run)
        worker.progress.connect(self.chat_import_progress)
        worker.item.connect(self.chat_folder_chat_found)
        worker.done.connect(self.chat_folder_import_completed)
        worker.error.connect(self.chat_folder_import_failed)
        worker.done.connect(thread.quit); worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda finished=thread:self.chatlist_thread_finished(finished))
        thread.start()

    def chat_import_progress(self,message):
        self.chat_status.setText(message)

    async def load_chat_folder(self,api_id,api_hash,session,slug,progress,on_chat=None):
        from telethon.tl.functions.chatlists import CheckChatlistInviteRequest
        from telethon.tl.types import Chat, User
        from telethon.tl.types.chatlists import ChatlistInvite, ChatlistInviteAlready
        from telethon.utils import get_peer_id
        progress('Подготавливаю соединение с Telegram...')
        client_options=await prepare_telegram_client()
        client=create_account_client(session,api_id,api_hash,**client_options)
        try:
            progress('Подключаю аккаунт к Telegram...')
            await connect_account_client(client)
            progress('Проверяю ссылку и содержимое папки...')
            invite=await client(CheckChatlistInviteRequest(slug))
            if isinstance(invite,ChatlistInvite):
                progress('Читаю список чатов из приглашения без вступления...')
                entities=list(invite.chats or [])+list(invite.users or [])
            elif isinstance(invite,ChatlistInviteAlready):
                progress('Папка уже добавлена аккаунту. Загружаю список чатов...')
                entities=list(invite.chats or [])+list(invite.users or [])
            else:
                raise RuntimeError(f'Telegram вернул неподдерживаемый тип приглашения: {type(invite).__name__}.')
            results=[]; seen=set()
            total=len(entities)
            for index,entity in enumerate(entities,1):
                if index==1 or index==total or index%10==0:
                    progress(f'Обрабатываю чаты из папки: {index} из {total}...')
                key=(type(entity).__name__,getattr(entity,'id',None))
                if key in seen: continue
                seen.add(key)
                if isinstance(entity,Chat):
                    title=entity.title; chat_type='Группа'
                elif isinstance(entity,User):
                    title=' '.join(part for part in (entity.first_name,entity.last_name) if part).strip() or (f'@{entity.username}' if entity.username else str(entity.id))
                    chat_type='Личный чат'
                elif getattr(entity,'megagroup',False):
                    title=entity.title; chat_type='Супергруппа'
                elif getattr(entity,'broadcast',False):
                    title=entity.title; chat_type='Канал'
                else:
                    title=getattr(entity,'title','')
                    chat_type='Чат'
                username=getattr(entity,'username',None)
                if not username:
                    active_usernames=[item.username for item in (getattr(entity,'usernames',None) or []) if getattr(item,'active',False)]
                    username=active_usernames[0] if active_usernames else None
                join_peer=f'@{username}' if username else ''
                chat=(title,str(get_peer_id(entity)),join_peer,chat_type)
                results.append(chat)
                if on_chat:
                    on_chat(chat)
            if not results:
                raise RuntimeError('В приглашении не найдено чатов для добавления.')
            return results
        finally:
            await client.disconnect()

    def chat_folder_imported(self,chats,session):
        if self.chat_folder_save_error: return
        self.chat_status.setText(f'Сохраняю чаты в программу: {len(chats)}...')
        try:
            for chat in chats:
                self.save_chat_folder_entry(chat,session)
            self.conn.commit()
        except sqlite3.Error as error:
            self.conn.rollback()
            self.chat_folder_import_failed(f'Чаты получены из Telegram, но не удалось сохранить их в базе: {error}')
            return
        self.refresh(); self.chat_progress.hide(); self.chat_status.setText(f'Импортировано чатов из папки: {len(chats)}. Сообщения не отправлялись.')
        QMessageBox.information(self,'Папка чатов',self.chat_status.text())

    def chat_folder_import_completed(self,chats):
        self.chat_folder_imported(chats,self.chat_folder_import_session)

    def save_chat_folder_entry(self,chat,session):
        title,peer,join_peer,chat_type=chat
        self.conn.execute('INSERT INTO chats(title,peer,join_peer,assigned_session,chat_type) VALUES(?,?,?,?,?) ON CONFLICT(peer) DO UPDATE SET title=excluded.title,join_peer=CASE WHEN excluded.join_peer!=\'\' THEN excluded.join_peer ELSE chats.join_peer END,assigned_session=CASE WHEN chats.assigned_session=\'\' THEN excluded.assigned_session ELSE chats.assigned_session END,chat_type=excluded.chat_type',(title,peer,join_peer,session,chat_type))

    def chat_folder_chat_found(self,payload):
        session,chat=payload
        if self.chat_folder_save_error: return
        try:
            self.save_chat_folder_entry(chat,session)
            self.conn.commit()
        except sqlite3.Error as error:
            self.conn.rollback()
            self.chat_folder_save_error=f'Не удалось сохранить чат «{chat[0]}» в базе: {error}'
            self.chat_folder_import_failed(self.chat_folder_save_error)
            return
        self.chat_folder_imported_count+=1
        self.refresh()
        self.chat_status.setText(f'Добавлено в список: {self.chat_folder_imported_count} чатов. Последний: {chat[0]}')

    def chat_folder_import_failed(self,error):
        self.chat_progress.hide()
        self.chat_status.setText(f'Не удалось импортировать папку чатов: {error}')
        QMessageBox.critical(self,'Импорт папки чатов',str(error))

    def chatlist_thread_finished(self,thread):
        if self.chatlist_thread is thread: self.chatlist_thread=None
        self.chat_folder_button.setEnabled(True); self.chat_progress.hide()
        thread.deleteLater()

    def add_chat(self):
        peer,ok=QInputDialog.getText(self,'Добавить общую группу','Ссылка на группу, @username или ID группы (-100...):')
        if not ok or not peer.strip():return
        if not self.account_box.currentData():
            self.chat_status.setText('Сначала импортируйте аккаунт TDATA.')
            QMessageBox.warning(self,'Нет аккаунта','Сначала импортируйте аккаунт TDATA.')
            return
        peer=peer.strip()
        peer=re.sub(r'^https?://t\.me/','',peer,flags=re.I).strip('/').split('?',1)[0]
        if peer.startswith('+') or peer.startswith('joinchat/'):
            QMessageBox.warning(self,'Приватная группа','Автоматическое вступление сейчас поддерживается для публичных групп по @username. Добавьте публичную группу по username или ссылке t.me/username.')
            return
        join_peer=''
        if not peer.lstrip('-').isdigit() and not peer.startswith('joinchat/'):
            join_peer='@'+peer.lstrip('@')
        try: api_id,api_hash=credentials()
        except Exception as e:
            self.chat_status.setText(f'Ошибка настроек API: {e}')
            QMessageBox.warning(self,'Настройки API',str(e))
            return
        self.chat_status.setText(f'Проверяю группу {peer} через Telegram...')
        self.status.setText(f'Проверяю группу {peer} через Telegram...')
        session=self.account_box.currentData(); self.thread=QThread(); self.worker=Worker(lambda:self.resolve_group(api_id,api_hash,session,peer,join_peer)); self.worker.moveToThread(self.thread); self.thread.started.connect(self.worker.run); self.worker.done.connect(self.group_resolved); self.worker.error.connect(lambda error:self.group_resolution_failed(error,session),Qt.QueuedConnection); self.worker.done.connect(self.thread.quit,Qt.DirectConnection); self.worker.error.connect(self.thread.quit,Qt.DirectConnection); self.thread.start()
    async def resolve_group(self,api_id,api_hash,session,peer,join_peer=''):
        from telethon.tl.types import Chat, Channel
        from telethon.utils import get_peer_id
        client_options=await prepare_telegram_client()
        client=create_account_client(session,api_id,api_hash,**client_options)
        try:
            try:
                await connect_account_client(client)
            except Exception as error:
                if account_issue_from_error(error)=='banned':
                    raise RuntimeError('ACCOUNT_BANNED: Telegram сообщил, что этот аккаунт заблокирован или деактивирован.') from error
                raise
            try: entity=await client.get_entity(peer)
            except Exception as e: raise RuntimeError(f'Не удалось найти «{peer}». Проверьте username и убедитесь, что выбранный аккаунт вступил в эту группу: {e}') from e
            if isinstance(entity,Chat) or (isinstance(entity,Channel) and getattr(entity,'megagroup',False)):
                chat_type='Группа' if isinstance(entity,Chat) else 'Супергруппа'
                return entity.title, str(get_peer_id(entity)), join_peer, chat_type
            if isinstance(entity,Channel): raise RuntimeError(f'«{peer}» является каналом, а не группой. Добавлять можно только группы и супергруппы.')
            raise RuntimeError(f'«{peer}» не является группой или супергруппой Telegram.')
        finally: await client.disconnect()
    def group_resolved(self,data):
        title,peer,join_peer,chat_type=data
        try:
            self.conn.execute('INSERT INTO chats(title,peer,join_peer,chat_type) VALUES(?,?,?,?) ON CONFLICT(peer) DO UPDATE SET title=excluded.title,join_peer=CASE WHEN excluded.join_peer!=\'\' THEN excluded.join_peer ELSE chats.join_peer END,chat_type=excluded.chat_type',(title,peer,join_peer,chat_type)); self.conn.commit(); self.refresh(); self.status.setText(f'Группа добавлена: {title}')
            self.chat_status.setText(f'Группа добавлена: {title}. Теперь выберите её на странице «Отправить сообщение».')
        except sqlite3.IntegrityError:
            self.chat_status.setText(f'Группа «{title}» уже добавлена.')
            QMessageBox.information(self,'Группа','Эта группа уже добавлена')
        finally: self.thread.quit()
    def group_resolution_failed(self,error,session=''):
        self.chat_status.setText(f'Не удалось добавить группу: {error}')
        self.failed(error,session)
    def send_message(self):
        if (self.chat_send_thread and self.chat_send_thread.isRunning()) or (self.thread and self.thread.isRunning()):
            QMessageBox.information(self,'Отправка','Дождитесь завершения текущей отправки.')
            return
        if not self.account_box.currentData() or not self.chat_box.currentData() or not self.message.toPlainText().strip(): QMessageBox.warning(self,'Недостаточно данных','Выберите аккаунт, чат и введите сообщение'); return
        try: api_id,api_hash=credentials()
        except Exception as e: QMessageBox.warning(self,'Настройки API',str(e)); return
        self.status.setText('Проверяю чат и отправляю...'); session=self.account_box.currentData(); peer=self.chat_box.currentData(); text=self.message.toPlainText().strip()
        chat_row=self.conn.execute('SELECT join_peer FROM chats WHERE peer=?',(str(peer),)).fetchone()
        join_peer=chat_row[0] if chat_row else ''
        self.thread=QThread(); self.worker=Worker(lambda:self.do_send(api_id,api_hash,session,peer,text,join_peer)); self.worker.moveToThread(self.thread); self.thread.started.connect(self.worker.run); self.worker.done.connect(self.sent); self.worker.error.connect(lambda error:self.failed(error,session),Qt.QueuedConnection); self.worker.done.connect(self.thread.quit,Qt.DirectConnection); self.worker.error.connect(self.thread.quit,Qt.DirectConnection); self.thread.start()
    def send_default_to_chat(self,chat_id,peer,session,button):
        if (self.chat_send_thread and self.chat_send_thread.isRunning()) or (self.thread and self.thread.isRunning()):
            self.chat_status.setText('Дождитесь завершения текущей отправки.')
            return
        if not session:
            QMessageBox.warning(self,'Не выбран аккаунт','Сначала назначьте аккаунт для этого чата.')
            return
        text=self.default_message.toPlainText().strip()
        if not text:
            QMessageBox.warning(self,'Нет сообщения','Введите сообщение по умолчанию перед отправкой.')
            return
        if not self.save_default_message(): return
        try: api_id,api_hash=credentials()
        except Exception as error:
            QMessageBox.warning(self,'Настройки API',str(error))
            return
        account=self.conn.execute('SELECT 1 FROM accounts WHERE session=?',(session,)).fetchone()
        if not account:
            QMessageBox.warning(self,'Аккаунт не найден','Назначенный аккаунт больше не добавлен. Выберите другой аккаунт для этого чата.')
            return
        row=self.conn.execute('SELECT join_peer FROM chats WHERE id=?',(chat_id,)).fetchone()
        join_peer=row[0] if row else ''
        thread=QThread(self); worker=Worker(lambda:self.do_send(api_id,api_hash,session,peer,text,join_peer))
        self.chat_send_thread=thread; self.chat_send_worker=worker
        worker.moveToThread(thread); thread.started.connect(worker.run)
        button.setEnabled(False); button.setText('Отправка...')
        self.chat_status.setText(f'Отправляю сообщение в «{self.conn.execute("SELECT title FROM chats WHERE id=?",(chat_id,)).fetchone()[0]}»...')
        worker.done.connect(lambda _:self.chat_default_send_finished(button,None),Qt.QueuedConnection)
        worker.error.connect(lambda error:self.chat_default_send_finished(button,error,session),Qt.QueuedConnection)
        worker.done.connect(thread.quit,Qt.DirectConnection); worker.error.connect(thread.quit,Qt.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda finished=thread:self.chat_send_thread_finished(finished))
        thread.start()
    def send_to_all_chats(self):
        if (self.chat_send_thread and self.chat_send_thread.isRunning()) or (self.thread and self.thread.isRunning()):
            QMessageBox.information(self,'Отправка','Дождитесь завершения текущей отправки.')
            return
        text=self.default_message.toPlainText().strip()
        if not text:
            QMessageBox.warning(self,'Нет сообщения','Введите сообщение по умолчанию перед запуском авторассылки.')
            return
        chats=list(self.conn.execute('SELECT title,peer,assigned_session,join_peer FROM chats ORDER BY title'))
        if not chats:
            QMessageBox.warning(self,'Нет чатов','Добавьте чаты перед запуском авторассылки.')
            return
        known_sessions={row[0] for row in self.conn.execute('SELECT session FROM accounts')}
        unassigned=[title for title,_,session,_ in chats if not session or session not in known_sessions]
        if unassigned:
            names=', '.join(f'«{title}»' for title in unassigned)
            QMessageBox.warning(self,'Не назначен аккаунт',f'Назначьте действующий аккаунт для каждого чата перед рассылкой:\n{names}')
            return
        if not self.save_default_message():
            return
        try:
            api_id,api_hash=credentials()
        except Exception as error:
            QMessageBox.warning(self,'Настройки API',str(error))
            return
        answer=QMessageBox.question(
            self,
            'Подтвердите авторассылку',
            f'Отправить сообщение по умолчанию во все {len(chats)} сохранённых чатов?\n'
            'Для каждого чата будет использован назначенный ему аккаунт.',
            QMessageBox.Yes|QMessageBox.No,
            QMessageBox.No,
        )
        if answer!=QMessageBox.Yes:
            return
        recipients=[(title,peer,session,join_peer or '') for title,peer,session,join_peer in chats]
        self.chat_status.setText(f'Авторассылка выполняется по {len(recipients)} чатам...')
        self.chat_broadcast_button.setEnabled(False)
        thread=QThread(self); worker=Worker(lambda:self.do_broadcast(api_id,api_hash,text,recipients))
        self.chat_send_thread=thread; self.chat_send_worker=worker
        worker.moveToThread(thread); thread.started.connect(worker.run)
        worker.done.connect(lambda result:self.chat_broadcast_finished(result,len(recipients)),Qt.QueuedConnection)
        worker.error.connect(self.chat_broadcast_failed)
        worker.done.connect(thread.quit,Qt.DirectConnection); worker.error.connect(thread.quit,Qt.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda finished=thread:self.chat_send_thread_finished(finished))
        thread.start()
    async def do_broadcast(self,api_id,api_hash,text,recipients):
        sent=[]; failures=[]
        for title,peer,session,join_peer in recipients:
            try:
                await self.do_send(api_id,api_hash,session,peer,text,join_peer)
                sent.append(title)
            except Exception as error:
                failures.append((title,str(error),session))
        return sent,failures
    def chat_broadcast_finished(self,result,total):
        sent,failures=result
        self.chat_status.setText(f'Авторассылка завершена: успешно отправлено в {len(sent)} из {total} чатов.')
        if failures:
            for _,error,session in failures:
                self.mark_account_issue(session,error,refresh=False)
            if any(account_issue_from_error(error) for _,error,_ in failures):
                self.refresh()
            details='\n'.join(f'• {title}: {error}' for title,error,_ in failures)
            QMessageBox.warning(self,'Авторассылка завершена',f'Успешно: {len(sent)} из {total}.\n\nНе удалось отправить:\n{details}')
        else:
            QMessageBox.information(self,'Авторассылка завершена',f'Сообщение отправлено во все {total} чатов.')
    def chat_broadcast_failed(self,error):
        self.chat_status.setText(f'Ошибка авторассылки: {error}')
        QMessageBox.critical(self,'Ошибка авторассылки',str(error))
    def chat_default_send_finished(self,button,error,session=''):
        button.setEnabled(True); button.setText('➤ Отправить')
        if error:
            self.mark_account_issue(session,error)
            message=str(error)
            lower=message.lower()
            if 'connection to telegram failed' in lower:
                message+=' Проверьте подключение к Интернету или прокси.'
            elif 'you can\'t write in this chat' in lower or 'sendmessagerequest' in lower:
                message='Telegram запретил отправку в этот чат. Проверьте участие аккаунта и права на публикацию.'
            self.chat_status.setText(f'Ошибка отправки: {message}')
            QMessageBox.critical(self,'Ошибка Telegram',message)
        else:
            self.chat_status.setText('Сообщение отправлено в выбранный чат.')
    def chat_send_thread_finished(self,thread):
        if self.chat_send_thread is thread: self.chat_send_thread=None
        self.chat_broadcast_button.setEnabled(True)
        thread.deleteLater()
    def sent(self,_): self.status.setText('Сообщение успешно отправлено'); self.message.clear(); self.thread.quit()
    def failed(self,e,session=''):
        e=visible_error_message(e)
        self.mark_account_issue(session,e)
        message=str(e)
        lower=message.lower()
        if 'connection to telegram failed' in lower:
            message+=' Проверьте подключение к Интернету или задайте прокси в «Настройках API».'
        elif 'you can\'t write in this chat' in lower or 'can\'t write in this chat' in lower or 'sendmessagerequest' in lower:
            message='Нельзя отправлять сообщения в этот чат. Проверьте, что выбранный аккаунт состоит в чате и имеет права на отправку сообщений; для каналов это обычно доступно только администратору.'
        elif 'flood' in lower:
            message='Telegram временно ограничил отправку сообщений. Подождите несколько минут и попробуйте снова.'
        self.status.setText('Ошибка операции'); QMessageBox.critical(self,'Ошибка Telegram',message); self.thread.quit()
    async def do_send(self,api_id,api_hash,session,peer,text,join_peer=''):
        from telethon.errors import BroadcastForbiddenError, ChannelPrivateError, ChatWriteForbiddenError, FloodWaitError, UserAlreadyParticipantError, UserBannedInChannelError
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.types import Channel
        client_options=await prepare_telegram_client()
        client=create_account_client(session,api_id,api_hash,**client_options)
        try:
            write_import_log(f'Начата отправка сообщения аккаунтом {Path(session).name} в чат {peer}.')
            try:
                await connect_account_client(client)
            except Exception as error:
                if account_issue_from_error(error)=='banned': 
                    raise RuntimeError('ACCOUNT_BANNED: Telegram сообщил, что этот аккаунт заблокирован или деактивирован.') from error
                raise
            peer_value=str(peer).strip()
            if peer_value.lstrip('-').isdigit():
                entity=int(peer_value)
            else:
                entity=peer_value
            lookup=join_peer or entity
            try:
                target=await telegram_call(client.get_entity(lookup),'поиск чата')
            except ValueError as exc:
                if not join_peer and isinstance(entity,int):
                    raise RuntimeError('Не удалось найти группу по сохранённому ID для этого аккаунта. Удалите её и добавьте заново по публичному @username, чтобы аккаунт мог вступить перед отправкой.') from exc
                raise
            if isinstance(target,Channel) and target.megagroup:
                try:
                    await telegram_call(client(JoinChannelRequest(target)),'вступление в группу')
                except UserAlreadyParticipantError:
                    pass
                target=await telegram_call(client.get_entity(target),'обновление данных группы')
            elif join_peer:
                raise RuntimeError('По указанной ссылке найден не публичная группа. Автоматическое вступление доступно для публичных групп по @username; приватные группы требуют invite-ссылку.')
            try:
                await telegram_call(client.send_message(target,text),'отправка сообщения')
            except (ChatWriteForbiddenError,BroadcastForbiddenError,ChannelPrivateError,UserBannedInChannelError) as exc:
                raise RuntimeError('Нельзя отправлять сообщения в этот чат. Проверьте, что аккаунт состоит в чате и имеет права на отправку сообщений; для каналов это обычно доступно только администратору.') from exc
            except FloodWaitError as exc:
                raise RuntimeError(f'Telegram временно ограничил отправку сообщений. Подождите {exc.seconds} секунд и попробуйте снова.') from exc
            write_import_log(f'Сообщение успешно отправлено аккаунтом {Path(session).name} в чат {peer}.')
            return True
        finally:
            await disconnect_account_client(client)

STYLE='''QMainWindow,QWidget{background:#10141d;color:#e8edf5;font-family:Segoe UI;font-size:13px}#side{background:#171d29;border-right:1px solid #293244}#brand{color:#64a8ff;font-size:19px;font-weight:700}#nav{background:transparent;border:0}#nav::item{padding:13px;margin:3px 0;color:#9ba8bd;border-radius:7px}#nav::item:selected{background:#263b5c;color:white}QPushButton{background:#263248;border:1px solid #34425a;border-radius:7px;padding:10px 15px;color:#e8edf5}QPushButton:hover{background:#31496d}#primary{background:#347bd1;border:0;font-weight:600}#action{text-align:left;padding:16px}#title{font-size:27px;font-weight:700;color:#f4f7fb}#muted{color:#8390a5}#card,#panel{background:#171e2b;border:1px solid #29364b;border-radius:10px}#card{padding:10px}#metric{font-size:28px;font-weight:700;color:#64a8ff}QTableWidget{background:#171e2b;alternate-background-color:#1b2535;border:1px solid #29364b;border-radius:8px;gridline-color:#29364b}QHeaderView::section{background:#202b3d;color:#aebbd0;padding:10px;border:0}QComboBox,QPlainTextEdit{background:#111823;border:1px solid #34425a;border-radius:6px;padding:9px;color:#e8edf5}QComboBox QAbstractItemView{background:#1b2535}'''
if __name__=='__main__':
    if sys.platform=='win32':
        import ctypes
        result=ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('TG.Manager.Desktop')
        if result: write_import_log(f'Не удалось назначить идентификатор значка приложения Windows: HRESULT {result}')
    app=QApplication(sys.argv); app.setWindowIcon(QIcon(str(ROOT/'tg-manager.ico'))); app.setStyleSheet(STYLE); win=Window(); win.show(); sys.exit(app.exec())
