from __future__ import annotations
import json, math, os, re, sqlite3, unicodedata, uuid, traceback, html, time, hmac
from contextlib import closing
from contextvars import ContextVar
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, quote_plus, urlparse, unquote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from html.parser import HTMLParser
from flask import Flask, jsonify, request, send_from_directory, abort, g, session, Response
from werkzeug.utils import secure_filename
import fitz
from openpyxl import load_workbook

ROOT = Path(getattr(__import__('sys'), '_MEIPASS', Path(__file__).resolve().parent))
WEB_DIR = ROOT / 'web'
ASSET_DIR = ROOT / 'assets'
TEMPLATE_PATH = Path(os.environ.get('DDS_TEMPLATE_PATH', ASSET_DIR / 'template_gps_galeao.pdf'))
LAYOUT_PATH = Path(os.environ.get('DDS_LAYOUT_PATH', ASSET_DIR / 'row_layout.json'))
if os.environ.get('DDS_DATA_DIR'):
    DATA_DIR = Path(os.environ['DDS_DATA_DIR'])
elif os.environ.get('LOCALAPPDATA'):
    DATA_DIR = Path(os.environ['LOCALAPPDATA']) / 'GrupoGPS' / 'PlataformaDDS'
else:
    DATA_DIR = Path(__file__).resolve().parent / 'data'
OUTPUT_DIR = DATA_DIR / 'outputs'
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = DATA_DIR / 'state.json'
TOPICS_DB_PATH = DATA_DIR / 'temas_offline.db'
MAX_UPLOAD_BYTES = 15 * 1024 * 1024

UNIT_CODE = 'GIG'  # Default/main unit used when a PDF is generated for all units.
WORK_SITES = ['GIG', 'TECA', 'UNITED', 'GATE GOURMET']
# The UI calls these work sites, while the institutional form prints them as UNIDADE.
UNITS = WORK_SITES
SITE_ALIASES = {
    'TERMINAL DE CARGA': 'TECA', 'CARGO TERMINAL': 'TECA',
    'UNITED AIRLINES': 'UNITED', 'GATEGOURMET': 'GATE GOURMET',
    'TPS 2': 'GIG', 'TPS2': 'GIG', 'TPS-2': 'GIG',
    'TPS 2 TERMINAL': 'GIG', 'TPS-2 TERMINAL': 'GIG', 'TERMINAL TPS2': 'GIG',
}
SHIFTS = ['DIURNO', 'VESPERTINO', 'NOTURNO']
UNIT_SUPERVISORS = {'UNITED': 'WILKER MARIA', 'GATE GOURMET': 'MARCIA FASUTINO', 'TECA': 'PEDRO PAULO'}
SUPERVISORS = {
    'NOTURNO': ['MARIA DO SOCORRO'],
    'DIURNO': ['ORLANDO BARBOSA', 'EVELYN MONTOVANI'],
    'VESPERTINO': ['KAYO LESSA', 'BRUNA FRAGA'],
}
# PADRAO RESPONSAVEIS APROVADO (GIG e demais unidades):
# numeracao na celula esquerda; nome centralizado com entreletras e sem fundo.
# As duas alturas sao as aprovadas para Orlando (primeira linha) e Evelyn (segunda).
RESPONSIBLE_NUMBER_CELL = (24.2, 47.2)
RESPONSIBLE_NAME_LEFT_INSET = 30.0
RESPONSIBLE_LETTER_SPACING = 0.35
RESPONSIBLE_ROW_ADJUST_Y = (4.0, 1.0)

SHIFT_ALIASES = {
    'NOTURNO':'NOTURNO','NOITE':'NOTURNO','MADRUGADA':'NOTURNO','TURNO NOTURNO':'NOTURNO',
    'DIURNO':'DIURNO','DIA':'DIURNO','MANHA':'DIURNO','MATUTINO':'DIURNO','MANHÃ':'DIURNO','TURNO DIURNO':'DIURNO',
    'VESPERTINO':'VESPERTINO','TARDE':'VESPERTINO','TURNO VESPERTINO':'VESPERTINO'
}

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path='/static')
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES
HOSTED_MODE = os.environ.get('DDS_HOSTED', '').strip() == '1'
ADMIN_USER = os.environ.get('DDS_ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('DDS_ADMIN_PASSWORD', '')
CENTRAL_AUTH_ENABLED = HOSTED_MODE and os.environ.get('DDS_CENTRAL_AUTH', '').strip() == '1'
OWNER_ACCESS_EMAIL = os.environ.get('DDS_OWNER_ACCESS_EMAIL', '').strip().lower()
OWNER_ACCESS_PASSWORD = os.environ.get('DDS_OWNER_ACCESS_PASSWORD', '')
OWNER_ACCESS_USER_ID = os.environ.get('DDS_OWNER_ACCESS_USER_ID', 'a85b328d-db23-44c0-9642-5109b9a13fe0').strip().lower()
OWNER_SESSION_SECRET = os.environ.get('DDS_OWNER_SESSION_SECRET', '')
if OWNER_SESSION_SECRET:
    app.secret_key = OWNER_SESSION_SECRET
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=HOSTED_MODE,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)
if HOSTED_MODE and not CENTRAL_AUTH_ENABLED and len(ADMIN_PASSWORD) < 16:
    raise RuntimeError('Hospedagem bloqueada: configure DDS_ADMIN_PASSWORD com pelo menos 16 caracteres antes de iniciar.')

# Separate every hosted customer into an isolated directory, even when requests run concurrently.
_CURRENT_TENANT_ID: ContextVar[str | None] = ContextVar('dds_current_tenant_id', default=None)

def _tenant_data_dir() -> Path:
    tenant_id = _CURRENT_TENANT_ID.get()
    if HOSTED_MODE and CENTRAL_AUTH_ENABLED and tenant_id:
        if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', tenant_id, re.I):
            raise RuntimeError('Identificador de conta inválido.')
        base = DATA_DIR / 'tenants' / tenant_id.lower()
    else:
        base = DATA_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base

def _template_settings_path() -> Path:
    return _tenant_data_dir() / 'template_settings.json'

def _active_template_path() -> Path:
    try:
        settings = json.loads(_template_settings_path().read_text(encoding='utf-8'))
        custom_name = settings.get('custom_filename') if settings.get('active') == 'custom' else None
        if custom_name and re.fullmatch(r'template_custom_[0-9a-f]{12}\.pdf', str(custom_name)):
            candidate = _tenant_data_dir() / 'templates' / custom_name
            if candidate.is_file():
                return candidate
    except (OSError, ValueError, TypeError):
        pass
    return TEMPLATE_PATH

def _state_path() -> Path:
    return _tenant_data_dir() / 'state.json'

def _topics_db_path() -> Path:
    return _tenant_data_dir() / 'temas_offline.db'

def _output_dir() -> Path:
    output = _tenant_data_dir() / 'outputs'
    output.mkdir(parents=True, exist_ok=True)
    return output

def _request_is_public_asset(path: str) -> bool:
    if path in ('/', '/healthz', '/api/health', '/api/owner-login', '/api/owner-session', '/api/owner-logout'):
        return True
    if path.startswith('/api/'):
        return False
    if path.startswith('/static/'):
        return True
    return Path(path).suffix.lower() in {'.js', '.css', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.ico', '.woff', '.woff2', '.ttf', '.webmanifest'}

def _authorize_central_request():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.lower().startswith('bearer '):
        return jsonify({'error': 'Entre na sua conta da Central para acessar o Gerador de DDS.'}), 401
    supabase_url = os.environ.get('SUPABASE_URL', '').strip().rstrip('/')
    publishable_key = os.environ.get('SUPABASE_ANON_KEY', '').strip()
    if not supabase_url or not publishable_key:
        app.logger.error('DDS central auth is enabled but SUPABASE_URL/SUPABASE_ANON_KEY are missing')
        return jsonify({'error': 'A autenticação central não está configurada no servidor.'}), 503
    body = json.dumps({'action': 'authorize', 'app_slug': 'gerador-dds'}).encode('utf-8')
    req = Request(
        supabase_url + '/functions/v1/gerador-dds-access',
        data=body,
        headers={'Authorization': auth_header, 'apikey': publishable_key, 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(req, timeout=8) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode('utf-8'))
        except Exception:
            payload = {}
        return jsonify({'error': payload.get('reason') or payload.get('error') or 'Acesso ao aplicativo não autorizado.'}), (401 if exc.code == 401 else 403 if exc.code in (400, 403, 404) else 503)
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        app.logger.warning('DDS central authorization unavailable: %s', type(exc).__name__)
        return jsonify({'error': 'Não foi possível validar a licença agora. Tente novamente quando o serviço central estiver disponível.'}), 503
    if not payload.get('allowed'):
        return jsonify({'error': payload.get('reason') or 'A licença do Gerador de DDS não está ativa.'}), 403
    user_id = str(payload.get('user_id') or '')
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', user_id, re.I):
        return jsonify({'error': 'A Central retornou uma identidade inválida.'}), 503
    g.dds_tenant_context_token = _CURRENT_TENANT_ID.set(user_id.lower())
    return None


def _owner_access_configured() -> bool:
    return bool(
        HOSTED_MODE and CENTRAL_AUTH_ENABLED
        and OWNER_ACCESS_EMAIL and len(OWNER_ACCESS_PASSWORD) >= 20
        and len(OWNER_SESSION_SECRET) >= 32 and app.secret_key
        and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', OWNER_ACCESS_USER_ID, re.I)
    )


@app.get('/api/owner-session')
def owner_session_status():
    enabled = _owner_access_configured()
    authenticated = enabled and hmac.compare_digest(
        str(session.get('dds_owner_user_id') or ''), OWNER_ACCESS_USER_ID
    )
    return jsonify({
        'enabled': enabled,
        'authenticated': bool(authenticated),
        'user_id': OWNER_ACCESS_USER_ID if authenticated else None,
    })


@app.post('/api/owner-login')
def owner_login():
    if not _owner_access_configured():
        return jsonify({'error': 'O acesso pessoal ainda não está configurado no servidor.'}), 503
    body = request.get_json(silent=True) or {}
    email = str(body.get('email') or '').strip().lower()
    password = str(body.get('password') or '')
    email_ok = hmac.compare_digest(email.encode('utf-8'), OWNER_ACCESS_EMAIL.encode('utf-8'))
    password_ok = hmac.compare_digest(password.encode('utf-8'), OWNER_ACCESS_PASSWORD.encode('utf-8'))
    if not (email_ok and password_ok):
        return jsonify({'error': 'E-mail ou senha do acesso pessoal inválidos.'}), 401
    session.clear()
    session['dds_owner_user_id'] = OWNER_ACCESS_USER_ID
    session.permanent = True
    return jsonify({'ok': True, 'user_id': OWNER_ACCESS_USER_ID})


@app.post('/api/owner-logout')
def owner_logout():
    session.clear()
    return jsonify({'ok': True})

@app.before_request
def require_hosted_admin_auth():
    if not HOSTED_MODE or CENTRAL_AUTH_ENABLED or request.path == '/healthz':
        return None
    auth = request.authorization
    username = auth.username if auth else ''
    password = auth.password if auth else ''
    user_ok = hmac.compare_digest(username.encode('utf-8'), ADMIN_USER.encode('utf-8'))
    pass_ok = hmac.compare_digest(password.encode('utf-8'), ADMIN_PASSWORD.encode('utf-8'))
    if not (user_ok and pass_ok):
        return jsonify({'error': 'Acesso restrito. Entre com as credenciais administrativas.'}), 401, {'WWW-Authenticate': 'Basic realm="Gerador de DDS", charset="UTF-8"'}
    return None

@app.before_request
def require_central_dds_license():
    if not CENTRAL_AUTH_ENABLED or _request_is_public_asset(request.path):
        return None
    owner_id = str(session.get('dds_owner_user_id') or '')
    if _owner_access_configured() and hmac.compare_digest(owner_id, OWNER_ACCESS_USER_ID):
        g.dds_tenant_context_token = _CURRENT_TENANT_ID.set(OWNER_ACCESS_USER_ID)
        return None
    return _authorize_central_request()

@app.teardown_request
def cleanup_central_tenant_context(_error=None):
    token = getattr(g, 'dds_tenant_context_token', None)
    if token is not None:
        try:
            _CURRENT_TENANT_ID.reset(token)
        except (RuntimeError, ValueError):
            pass
        g.dds_tenant_context_token = None

@app.before_request
def protect_local_mutations():
    # Local installs remain loopback-only. Hosted installs rely on HTTPS plus mandatory admin auth.
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        if not HOSTED_MODE and request.remote_addr not in {'127.0.0.1', '::1'}:
            return jsonify({'error': 'A plataforma aceita alterações apenas neste computador.'}), 403
        origin = request.headers.get('Origin')
        if origin:
            parsed = urlsplit(origin)
            expected_scheme = 'https' if HOSTED_MODE else 'http'
            # Render terminates HTTPS at its proxy, so the internal request host may differ
            # from the public browser origin. Accept only the app host reported by Render
            # or the same request host; do not enable cross-origin requests.
            allowed_hosts = {request.host.lower()}
            render_host = os.environ.get('RENDER_EXTERNAL_HOSTNAME', '').strip().lower()
            if render_host:
                allowed_hosts.add(render_host)
            forwarded_host = request.headers.get('X-Forwarded-Host', '').split(',')[0].strip().lower()
            if forwarded_host:
                allowed_hosts.add(forwarded_host)
            if parsed.scheme != expected_scheme or parsed.netloc.lower() not in allowed_hosts or parsed.path not in ('', '/'):
                return jsonify({'error': 'Solicitação de outra origem bloqueada.'}), 403
    return None


def persistent_storage_available() -> bool:
    """Confirm the hosted data directory is an actual mounted volume."""
    if not HOSTED_MODE:
        return True
    configured = os.environ.get('DDS_DATA_DIR', '').strip()
    if not configured:
        return False
    target = Path(configured).resolve()
    try:
        for entry in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines():
            fields = entry.split(' - ', 1)[0].split()
            if len(fields) < 5:
                continue
            mountpoint = fields[4].replace(r'\040', ' ').replace(r'\011', chr(9)).replace(r'\134', chr(92))
            if Path(mountpoint).resolve() == target:
                return True
    except (OSError, ValueError):
        return False
    return False


def storage_status() -> dict:
    persistent = persistent_storage_available()
    if not HOSTED_MODE:
        label = 'Dados salvos neste computador.'
    elif persistent:
        label = 'Armazenamento persistente do servidor.'
    else:
        label = 'Cópia automática neste navegador; não compartilhada entre dispositivos.'
    return {
        'storage_scope': 'server' if HOSTED_MODE else 'local',
        'persistent_storage_configured': persistent,
        'storage_label': label,
    }


# Resposta pública expõe somente saúde booleana, nunca credenciais.
# A chamada ao cofre é limitada a uma por minuto por processo.
_VAULT_HEALTH_CACHE = {'checked_at': -1.0, 'ready': False}


def check_durable_vault_health() -> bool:
    if not HOSTED_MODE:
        return False
    now=time.monotonic()
    if 0 <= now - _VAULT_HEALTH_CACHE['checked_at'] < 60.0:
        return bool(_VAULT_HEALTH_CACHE['ready'])
    endpoint=os.environ.get('DDS_VAULT_ENDPOINT','').strip().rstrip('/')
    token=os.environ.get('DDS_VAULT_OWNER_TOKEN','').strip()
    ready=False
    if endpoint.startswith('https://') and len(token)>=50:
        try:
            req=Request(endpoint+'?action=ping',method='GET',
                headers={'Accept':'application/json',
                         'apikey':'sb_publishable_h1TBieh4mxQj0DZXDNu31w_C9HDenLR',
                         'X-DDS-Owner-Token':token})
            with urlopen(req,timeout=6) as response:
                payload=json.loads(response.read(4096).decode('utf-8'))
                ready=(response.status==200 and payload.get('vault_ready') is True)
        except (HTTPError,URLError,TimeoutError,OSError,ValueError):
            ready=False
    _VAULT_HEALTH_CACHE.update(checked_at=now,ready=ready)
    return ready


@app.get('/healthz')
def healthz():
    return jsonify({'ok': True, 'service': 'Gerador de DDS', 'hosted': HOSTED_MODE,
                    'persistent_storage_configured': persistent_storage_available(),
                    'durable_vault_ready': check_durable_vault_health()})


def normalize(value: Any) -> str:
    if value is None: return ''
    text = unicodedata.normalize('NFKD', str(value)).encode('ascii','ignore').decode('ascii')
    return re.sub(r'\s+', ' ', text).strip().upper()


def normalize_shift(value: Any) -> str:
    s = normalize(value)
    return SHIFT_ALIASES.get(s, s if s in SHIFTS else '')


def slugify(text: str, limit=60) -> str:
    s=normalize(text).lower()
    s=re.sub(r'[^a-z0-9]+','-',s).strip('-')
    return (s[:limit].strip('-') or 'dds')


def default_state() -> dict:
    return {'employees': [], 'history': [], 'settings': {'company':'TOP SERVICE - LIMPEZA','site':'GALEÃO','unit_code':'GIG','duration':'01:00'}}


TOPIC_SEEDS = [
    ('Respeito à hierarquia','Ética e relações profissionais','Respeite as responsabilidades de supervisores, líderes e colegas. Ouça instruções com atenção, esclareça dúvidas com educação, cumpra orientações lícitas e seguras e comunique conflitos pelos canais apropriados. A hierarquia não autoriza humilhação, assédio ou ordens que exponham pessoas a riscos. Ao perceber perigo, interrompa a atividade com segurança e procure a liderança.'),
    ('Prevenção de quedas e escorregões','Prevenção de acidentes','Sinalize pisos molhados, mantenha rotas livres, use calçado adequado e comunique condições inseguras.'),
    ('Uso correto de EPI','Equipamentos de proteção','Confira o estado do EPI, use o equipamento indicado para a tarefa, não improvise e comunique danos ou falta de material.'),
    ('Manuseio seguro de produtos químicos','Produtos químicos','Leia rótulos e fichas de segurança, nunca misture produtos, mantenha embalagens identificadas e utilize a proteção indicada.'),
    ('Prevenção de riscos biológicos','Saúde e higiene','Siga os procedimentos de higiene, utilize barreiras de proteção indicadas e comunique exposições, acidentes ou materiais suspeitos.'),
    ('Descarte correto de resíduos','Resíduos e meio ambiente','Separe os resíduos conforme o procedimento local, não compacte sacos com as mãos e nunca coloque a mão dentro de recipientes.'),
    ('Ergonomia e postura no trabalho','Ergonomia','Planeje movimentos, evite torções, mantenha cargas próximas ao corpo, use meios auxiliares e peça ajuda quando necessário.'),
    ('Levantamento e transporte manual de cargas','Ergonomia','Avalie peso e percurso antes de movimentar, utilize carrinhos quando disponíveis e peça apoio para cargas difíceis ou volumosas.'),
    ('Organização e limpeza do ambiente','Organização e limpeza','Guarde materiais após o uso, mantenha passagens livres, sinalize riscos e não bloqueie saídas ou equipamentos de emergência.'),
    ('Prevenção de cortes e perfurações','Prevenção de acidentes','Inspecione materiais antes de recolher, não pressione sacos com as mãos e descarte objetos cortantes em recipientes próprios.'),
    ('Uso seguro de escadas e acessos','Prevenção de acidentes','Inspecione a escada, apoie-a em superfície estável, mantenha três pontos de contato e não improvise acessos.'),
    ('Segurança na movimentação de carrinhos','Movimentação de materiais','Confira rodas e carga, mantenha a visão do caminho, evite excesso de peso e respeite áreas de circulação.'),
    ('Prevenção de acidentes no trajeto','Segurança no trânsito','Planeje o deslocamento, respeite sinalização, evite distrações e nunca conduza após consumir álcool ou substâncias que afetem a atenção.'),
    ('Atenção e percepção de riscos','Percepção de riscos','Antes de iniciar, observe o local, identifique perigos, avalie as consequências e interrompa a tarefa se houver risco não controlado.'),
    ('Comunicação de incidentes e quase acidentes','Comunicação e prevenção','Comunique prontamente incidentes, quase acidentes e condições inseguras para permitir investigação e prevenção de recorrências.'),
    ('Prevenção de incêndios','Emergência','Mantenha saídas e equipamentos desobstruídos, conheça o alarme e a rota de fuga e não combata incêndio sem treinamento e condições seguras.'),
    ('Rotas de fuga e abandono de área','Emergência','Conheça as rotas e pontos de encontro, siga as orientações da brigada e não utilize elevadores durante abandono de emergência.'),
    ('Higienização das mãos','Saúde e higiene','Higienize as mãos nos momentos indicados, siga a técnica orientada e evite tocar rosto e superfícies limpas com as mãos contaminadas.'),
    ('Prevenção de contaminação cruzada','Saúde e higiene','Use materiais separados conforme o procedimento, respeite a sequência de limpeza e troque panos e utensílios quando indicado.'),
    ('Sinalização de área molhada','Organização e limpeza','Isole e sinalize a área antes de iniciar, mantenha a sinalização até o piso estar seguro e redirecione a circulação quando necessário.'),
    ('Uso seguro de equipamentos de limpeza','Equipamentos e ferramentas','Inspecione cabos, plugues e proteções, utilize o equipamento conforme instruções e retire de uso itens danificados.'),
    ('Segurança elétrica básica','Riscos elétricos','Não toque em cabos danificados, não faça reparos improvisados, mantenha equipamentos longe de água e acione pessoal autorizado.'),
    ('Prevenção de fadiga e atenção','Saúde ocupacional','Respeite pausas previstas, mantenha comunicação com a equipe e informe à liderança quando cansaço ou mal-estar afetar a execução segura.'),
    ('Respeito e trabalho em equipe','Comportamento seguro','Compartilhe informações relevantes, confirme instruções importantes e ofereça apoio sem executar tarefas para as quais não esteja autorizado.'),
    ('Proteção de pedestres em áreas operacionais','Circulação segura','Utilize rotas autorizadas, observe veículos e equipamentos móveis, respeite barreiras e mantenha contato visual quando aplicável.'),
    ('Prevenção de derramamentos','Produtos químicos','Isole a área, evite contato direto, siga o procedimento local de resposta e comunique a liderança; não tente limpar material desconhecido.'),
    ('Uso racional de água e produtos','Meio ambiente','Utilize somente a quantidade necessária conforme procedimento, evite desperdício e comunique vazamentos ou embalagens com problemas.'),
    ('Proteção auditiva e ruído','Saúde ocupacional','Respeite áreas sinalizadas, utilize proteção auditiva quando indicada e comunique ruídos anormais ou mudanças no ambiente.'),
    ('Trabalho seguro em condições de calor','Saúde ocupacional','Mantenha hidratação conforme orientação local, faça pausas previstas e comunique tontura, fraqueza ou outros sinais de mal-estar.'),
    ('Procedimento de trabalho seguro','Procedimentos operacionais','Confirme a orientação antes de começar, utilize ferramentas adequadas e não altere etapas críticas sem autorização.'),
    ('Direito de interromper atividade insegura','Cultura de segurança','Se identificar perigo grave ou condição não controlada, pare a atividade com segurança, sinalize quando possível e procure a liderança.'),
    ('Limpeza de sanitários de alto fluxo','Limpeza aeroportuária','Organize a sequência de trabalho, use os produtos e materiais previstos no procedimento, evite cruzar utensílios entre sanitários e outras áreas, respeite o tempo de contato indicado no rótulo e sinalize o piso até estar seguro. Comunique falta de insumos, avarias e qualquer exposição.'),
    ('Limpeza em áreas de embarque e circulação','Limpeza aeroportuária','Planeje a tarefa sem bloquear rotas acessíveis, saídas, sinalização ou fluxo de passageiros. Isole e sinalize a área conforme o procedimento, mantenha carrinhos sob controle, observe malas e equipamentos em movimento e combine a intervenção com a liderança quando houver impacto na circulação.'),
    ('Prevenção de contaminação cruzada na limpeza','Limpeza aeroportuária','Use panos, mops e utensílios separados conforme o procedimento local e a identificação adotada pela empresa. Respeite a sequência de limpeza, higienize ou substitua os materiais conforme orientação e não leve utensílio de sanitário para superfícies de contato em outras áreas.'),
    ('Resposta a derramamento de origem desconhecida','Emergência operacional','Não toque, não cheire de perto e não tente neutralizar substância desconhecida. Afaste pessoas com segurança, sinalize/isole a área quando puder fazê-lo sem exposição e comunique imediatamente a liderança e a equipe responsável pela resposta. Siga o procedimento local e não retome a limpeza sem liberação.'),
    ('Segurança da limpeza em áreas operacionais','Limpeza aeroportuária','Acesse somente áreas para as quais esteja autorizado e siga as regras de circulação, identificação, acompanhamento e comunicação locais. Mantenha distância segura de veículos e equipamentos, não atravesse rotas operacionais fora dos pontos permitidos e interrompa a tarefa se perder contato visual ou se surgir condição não controlada.'),
    ('Uso seguro de saneantes na limpeza profissional','Produtos químicos','Confirme identificação, finalidade, diluição, compatibilidade e proteção indicada no rótulo e na FDS disponível. Nunca misture produtos por iniciativa própria, não reutilize embalagem para outro conteúdo e não prepare solução sem treinamento e utensílios previstos. Em exposição ou reação inesperada, afaste-se e acione a resposta definida no local.'),

    # Catálogo ampliado de títulos DDS — temas alinhados às NRs vigentes e às
    # orientações públicas de SST da Fundacentro/MTE. Conteúdo deve ser validado
    # com os procedimentos e riscos reais de cada unidade.
    ('Gerenciamento de riscos ocupacionais e PGR','Gestão de riscos','Identifique perigos, avalie riscos conforme o procedimento da empresa, respeite as medidas de prevenção definidas no PGR e comunique mudanças nas condições de trabalho.'),
    ('Como reconhecer perigos antes de iniciar a tarefa','Percepção de riscos','Faça uma pausa antes de começar, observe pessoas, equipamentos, produtos e circulação; identifique o que pode causar dano e só prossiga quando as medidas de controle estiverem disponíveis.'),
    ('Hierarquia das medidas de prevenção','Gestão de riscos','Priorize eliminar o perigo quando possível, adote medidas coletivas e administrativas e utilize EPI como parte do controle previsto, nunca como justificativa para ignorar riscos.'),
    ('Direito de recusa e comunicação de risco grave','Cultura de segurança','Não inicie ou continue uma atividade diante de risco grave e iminente. Afaste-se com segurança, avise a liderança e siga o procedimento interno para avaliação e liberação.'),
    ('Diálogo Diário de Segurança: participação da equipe','Cultura de segurança','Compartilhe situações reais, escute dúvidas sem constrangimento, confirme se todos entenderam as orientações e registre riscos que precisam de acompanhamento.'),
    ('Investigação de incidentes sem busca por culpados','Comunicação e prevenção','Relate fatos, condições e barreiras que falharam. Preserve informações úteis, coopere com a análise e concentre a prevenção em corrigir causas e evitar repetição.'),
    ('Quase acidente: por que todo relato importa','Comunicação e prevenção','Informe também ocorrências sem lesão ou dano. Um quase acidente revela uma falha de controle que pode ser corrigida antes de uma consequência mais grave.'),
    ('Inspeção de segurança no início do turno','Inspeção de segurança','Verifique piso, iluminação, rotas, equipamentos, cabos, carrinhos, produtos e sinalização. Registre anomalias e não use item danificado até avaliação autorizada.'),
    ('Uso, conservação e troca de EPI','Equipamentos de proteção','Use o EPI indicado para o risco, confira ajuste e integridade, higienize e guarde conforme orientação e solicite substituição quando houver dano, contaminação ou perda de proteção.'),
    ('Seleção correta do EPI para cada atividade','Equipamentos de proteção','Confirme o risco e a orientação de segurança antes de selecionar a proteção. Não substitua modelos por conta própria e consulte a liderança quando houver dúvida de compatibilidade.'),
    ('Proteção das mãos durante a limpeza','Equipamentos de proteção','Selecione luvas adequadas ao produto e à tarefa, confira furos e desgaste, evite tocar o rosto e higienize as mãos após retirar as luvas. Luvas não tornam seguro o contato com substâncias desconhecidas.'),
    ('Proteção ocular contra respingos','Equipamentos de proteção','Utilize proteção ocular quando prevista na avaliação de risco, verifique ajuste e limpeza e nunca aplique produto acima do nível dos olhos ou sem controle contra respingos.'),
    ('Proteção respiratória e controle de exposição','Saúde ocupacional','Não escolha respirador por conta própria. Siga a avaliação de risco, o programa e o treinamento aplicáveis, confira vedação e comunique desconforto ou falha.'),
    ('Calçado de segurança e prevenção de quedas','Equipamentos de proteção','Use calçado adequado, fechado e em bom estado, mantenha solado limpo e observe pisos molhados, desníveis e resíduos que possam causar escorregões.'),
    ('Armazenamento seguro de saneantes','Produtos químicos','Mantenha produtos identificados, fechados e em local autorizado, separados conforme compatibilidade e instruções do rótulo. Nunca armazene saneantes em garrafas de bebidas.'),
    ('Leitura de rótulos e Fichas com Dados de Segurança','Produtos químicos','Antes do uso, confira identificação, finalidade, perigos, diluição, primeiros cuidados e EPI no rótulo e na FDS disponível. Em caso de dúvida, não utilize até receber orientação.'),
    ('Diluição correta de produtos de limpeza','Produtos químicos','Prepare soluções somente quando autorizado e treinado, usando a dosagem e os instrumentos indicados. Não misture produtos nem altere concentrações para acelerar o serviço.'),
    ('Incompatibilidade química: nunca misturar saneantes','Produtos químicos','Nunca misture produtos, especialmente água sanitária com ácidos ou amônia. Reações podem liberar gases perigosos. Afaste-se e comunique qualquer mistura acidental.'),
    ('Resposta segura a exposição química','Produtos químicos','Interrompa a atividade e siga o plano local de emergência e as instruções da FDS. Não improvise neutralização ou tratamento; comunique imediatamente a liderança e procure atendimento conforme orientação.'),
    ('Transporte manual de embalagens químicas','Produtos químicos','Confira fechamento e identificação, use meios de transporte previstos e não carregue recipientes danificados. Evite levantar cargas acima da capacidade e peça apoio quando necessário.'),
    ('Prevenção de vazamentos durante o preparo de soluções','Produtos químicos','Prepare soluções em local autorizado, sobre superfície estável e com utensílios adequados. Feche embalagens após o uso e informe vazamentos sem tocar em substância desconhecida.'),
    ('Armazenamento e segregação de resíduos','Resíduos e meio ambiente','Separe resíduos conforme classificação e procedimento local, use recipientes identificados e mantenha tampas e áreas de armazenamento em condições seguras.'),
    ('Coleta segura de resíduos e sacos de lixo','Resíduos e meio ambiente','Não comprima sacos, não introduza as mãos em lixeiras e observe perfurações ou peso excessivo. Feche e transporte conforme procedimento, comunicando resíduos suspeitos.'),
    ('Descarte de perfurocortantes encontrados na limpeza','Resíduos e meio ambiente','Não recolha objetos cortantes com as mãos nem os esconda em sacos comuns. Isole a situação e acione o procedimento e a equipe autorizada para descarte seguro.'),
    ('Redução de desperdício de água na limpeza','Meio ambiente','Use água conforme a necessidade e o procedimento, feche torneiras e comunique vazamentos. Evite deixar mangueiras abertas ou criar áreas molhadas sem sinalização.'),
    ('Uso responsável de produtos e materiais','Meio ambiente','Retire apenas a quantidade necessária, siga a diluição indicada e evite descartes desnecessários. Comunique desperdícios recorrentes e embalagens com defeito.'),
    ('Ergonomia no uso de mop, rodo e vassoura','Ergonomia','Ajuste o cabo quando possível, mantenha postura confortável, alterne movimentos sem torcer o tronco e evite trabalhar com braços excessivamente elevados.'),
    ('Prevenção de lesões por movimentos repetitivos','Ergonomia','Siga a organização de trabalho e pausas previstas, alterne tarefas quando autorizado e comunique dor ou formigamento no início, sem esperar o problema piorar.'),
    ('Movimentação segura de mobiliário e objetos','Ergonomia','Avalie peso, tamanho, percurso e obstáculos. Use ajuda ou equipamento auxiliar, mantenha dedos fora de pontos de esmagamento e não movimente cargas que impeçam a visão.'),
    ('Empurrar e puxar carrinhos com segurança','Ergonomia','Mantenha carga estável e dentro da capacidade, verifique rodas e alças, use força controlada e evite puxar de forma que bloqueie a visão ou force a coluna.'),
    ('Prevenção de quedas no mesmo nível','Prevenção de acidentes','Mantenha pisos sem obstáculos, sinalize áreas molhadas, use calçado adequado e observe desníveis, tapetes, cabos e mudanças de superfície.'),
    ('Uso seguro de escadas portáteis','Prevenção de acidentes','Inspecione degraus e travas, coloque a escada em piso firme e não improvise apoios. Não suba com as mãos ocupadas nem exceda a altura e a capacidade permitidas.'),
    ('Trabalho em altura: autorização e proteção','Prevenção de acidentes','Não execute trabalho em altura sem capacitação, autorização, análise de risco e proteções previstas. Utilize sistemas definidos e nunca improvise ancoragens ou acesso.'),
    ('Prevenção de choque elétrico em áreas molhadas','Riscos elétricos','Não manuseie plugues ou equipamentos elétricos com mãos molhadas ou cabos danificados. Afaste a água, isole a área e acione profissional autorizado.'),
    ('Inspeção de cabos, plugues e extensões','Riscos elétricos','Confira integridade antes do uso, evite esmagamento e passagem em áreas molhadas e retire de serviço itens com aquecimento, emendas improvisadas ou isolamento danificado.'),
    ('Bloqueio e sinalização antes de manutenção','Riscos elétricos','Não abra, desmonte ou tente reparar equipamento. A intervenção deve ser realizada por profissional autorizado, com isolamento de energias e procedimento específico.'),
    ('Uso seguro de máquinas e equipamentos de limpeza','Equipamentos e ferramentas','Leia as instruções, confira proteções, cabos e acessórios, não remova dispositivos de segurança e desligue conforme procedimento antes de limpar ou trocar componentes.'),
    ('Retirada de equipamento defeituoso de serviço','Equipamentos e ferramentas','Pare de usar o equipamento, desconecte apenas se for seguro e previsto, identifique a falha e comunique a liderança. Não faça reparos improvisados.'),
    ('Segurança no uso de lavadora de piso','Equipamentos e ferramentas','Receba treinamento, confira escovas, cabos, bateria e reservatórios, sinalize a área e mantenha distância segura de pessoas. Não opere em condição para a qual o equipamento não foi projetado.'),
    ('Segurança no uso de aspiradores industriais','Equipamentos e ferramentas','Verifique filtro, cabo, plugue e recipiente, use o modelo apropriado ao material e não aspire líquidos, poeiras perigosas ou substâncias desconhecidas sem autorização específica.'),
    ('Prevenção de incêndios em áreas de trabalho','Emergência','Mantenha saídas, extintores e alarmes desobstruídos, reconheça os meios de abandono e comunique cheiro de queimado, fumaça ou aquecimento anormal.'),
    ('Plano de abandono e ponto de encontro','Emergência','Conheça alarmes, rotas e ponto de encontro da unidade. Ao receber orientação, interrompa a tarefa com segurança, não retorne para buscar objetos e siga a equipe responsável.'),
    ('Uso de extintores: limites e segurança','Emergência','Priorize alertar, acionar o plano de emergência e abandonar a área quando necessário. Só utilize extintor se treinado, com rota de fuga livre e sem se expor.'),
    ('Comunicação de emergência no aeroporto','Emergência operacional','Conheça os canais e contatos locais, informe localização precisa, tipo de ocorrência e riscos observados. Não divulgue informações não confirmadas nem atrase o acionamento da equipe responsável.'),
    ('Circulação segura perto de veículos operacionais','Circulação segura','Use rotas autorizadas, observe pontos cegos, mantenha distância de veículos em movimento e só atravesse nos locais permitidos, após confirmar que a passagem é segura.'),
    ('Segurança em áreas restritas do aeroporto','Segurança aeroportuária','Acesse somente áreas autorizadas, porte a identificação exigida e siga escolta, barreiras e regras locais. Não deixe carrinhos ou materiais bloqueando rotas operacionais.'),
    ('Prevenção de atropelamentos em áreas de serviço','Circulação segura','Mantenha-se nas rotas de pedestres, evite celular durante deslocamentos e estabeleça contato visual com operadores quando aplicável. Nunca presuma que o condutor já viu você.'),
    ('Condução segura de carrinhos em locais movimentados','Movimentação de materiais','Controle a velocidade, mantenha a carga estável e a visão do caminho, reduza em curvas e portas e dê prioridade à circulação conforme as regras locais.'),
    ('Prevenção de colisões em corredores e portas','Circulação segura','Aproxime-se de cruzamentos sem correr, mantenha carrinhos sob controle e observe o fluxo nos dois sentidos. Não bloqueie portas, saídas ou rotas acessíveis.'),
    ('Limpeza segura de escadas e corrimãos','Limpeza aeroportuária','Trabalhe por etapas, sinalize o piso e preserve passagem segura. Não deixe ferramentas nos degraus e não execute a tarefa se o fluxo impedir isolamento adequado.'),
    ('Limpeza segura de elevadores e áreas de acesso','Limpeza aeroportuária','Coordene a atividade conforme a rotina local, não bloqueie portas ou dispositivos de emergência e mantenha ferramentas e cabos fora da circulação de passageiros.'),
    ('Limpeza de áreas de alimentação e praças de refeição','Limpeza aeroportuária','Siga os requisitos de higiene da área, separe utensílios conforme o procedimento e evite contaminação de superfícies de contato com materiais usados em sanitários ou resíduos.'),
    ('Limpeza de superfícies de alto contato','Limpeza aeroportuária','Identifique superfícies previstas no procedimento, use produto e tempo de contato indicados e troque ou higienize os materiais para não transportar contaminantes entre áreas.'),
    ('Limpeza segura de vidros e superfícies elevadas','Limpeza aeroportuária','Avalie altura, acesso e produto antes de iniciar. Utilize apenas equipamentos autorizados e treinamentos exigidos; não se apoie em mobiliário nem improvise plataformas.'),
    ('Sinalização e isolamento durante a limpeza','Organização e limpeza','Posicione placas e barreiras antes de iniciar, de forma visível e sem criar novo obstáculo. Mantenha-as até a área estar segura e libere a circulação somente após conferência.'),
    ('Organização de carrinhos e materiais de limpeza','Organização e limpeza','Distribua materiais para evitar queda e vazamento, mantenha produtos fechados e identificados e nunca deixe utensílios em escadas, corredores, portas ou rotas de fuga.'),
    ('Controle de chaves, acessos e áreas autorizadas','Procedimentos operacionais','Siga os controles de acesso da unidade, não empreste credenciais e comunique perda, dano ou acesso indevido imediatamente conforme o procedimento local.'),
    ('Identificação e comunicação de objetos suspeitos','Emergência operacional','Não toque nem mova objeto suspeito. Afaste-se, preserve a área sem se expor e comunique imediatamente a segurança ou o canal definido pela unidade.'),
    ('Proteção de bagagens e pertences de passageiros','Conduta profissional','Não abra, mova ou manipule bagagens sem autorização prevista no procedimento. Informe objetos esquecidos ou ocorrências ao setor responsável e preserve a privacidade dos passageiros.'),
    ('Conduta segura diante de sangue e fluidos corporais','Riscos biológicos','Evite contato direto, isole a área e utilize somente o procedimento e os materiais previstos. Não recolha perfurocortantes com as mãos e comunique a equipe autorizada.'),
    ('Prevenção de exposição a agentes biológicos','Riscos biológicos','Siga higiene das mãos, EPI e procedimentos de limpeza definidos para o risco. Evite respingos e contato com mucosas e comunique imediatamente exposição ou falha de proteção.'),
    ('Higiene das mãos antes e depois das tarefas','Saúde e higiene','Higienize as mãos nos momentos previstos, inclusive após retirar luvas e após contato com resíduos. Luvas não substituem a higiene das mãos.'),
    ('Etiqueta respiratória e prevenção de transmissão','Saúde e higiene','Adote higiene das mãos, etiqueta respiratória e medidas definidas para a situação local. Comunique sintomas relevantes conforme as regras da empresa e não compartilhe itens de uso pessoal.'),
    ('Prevenção de contaminação em utensílios reutilizáveis','Saúde e higiene','Limpe, desinfete ou substitua utensílios conforme o procedimento e a finalidade. Não misture materiais de áreas diferentes e armazene os itens limpos separados dos usados.'),
    ('Saúde ocupacional: comunicar sintomas precocemente','Saúde ocupacional','Informe sintomas ou desconfortos que possam afetar a execução segura e siga as orientações do serviço de saúde ocupacional. Não se automedique nem esconda uma exposição.'),
    ('Prevenção de estresse térmico e desidratação','Saúde ocupacional','Observe as condições ambientais, siga pausas e hidratação previstas e comunique tontura, fraqueza, confusão ou mal-estar. Procure apoio imediatamente diante de sinais preocupantes.'),
    ('Fadiga, sono e segurança no turno','Saúde ocupacional','Reconheça sinais de cansaço, distração e reação lenta. Siga as pausas e comunique à liderança quando a fadiga comprometer a segurança, especialmente em áreas operacionais.'),
    ('Riscos psicossociais e organização do trabalho','Saúde ocupacional','Trate sobrecarga, falta de clareza, conflitos e pressão inadequada como assuntos de prevenção. Utilize os canais internos e contribua para um ambiente respeitoso e seguro.'),
    ('Prevenção de assédio moral e sexual no trabalho','Respeito e conduta','Mantenha conduta respeitosa, não normalize humilhação ou comportamento sexual indesejado e utilize os canais de denúncia e apoio definidos pela organização. Respeite a confidencialidade.'),
    ('Respeito à diversidade e prevenção de discriminação','Respeito e conduta','Trate todas as pessoas com respeito, evite comentários discriminatórios e comunique situações de assédio ou exclusão pelos canais adequados, sem retaliação.'),
    ('Comunicação clara na troca de turno','Comunicação e prevenção','Informe pendências, áreas isoladas, equipamentos defeituosos, produtos em falta e riscos não resolvidos. Confirme o recebimento das informações importantes pela equipe seguinte.'),
    ('Passagem de serviço segura entre equipes','Comunicação e prevenção','Transmita o estado da área, sinalizações em vigor, incidentes e restrições. Não retire barreiras nem considere uma tarefa concluída sem verificar as condições de segurança.'),
    ('Prevenção de distrações com celular no trabalho','Comportamento seguro','Guarde o celular durante tarefas e deslocamentos que exijam atenção. Utilize-o somente em local e momento permitidos, sem comprometer circulação, operação ou atendimento.'),
    ('Pontualidade, preparação e segurança no início do turno','Procedimentos operacionais','Comece com orientação recebida, uniforme e EPI previstos, materiais conferidos e conhecimento das prioridades. Não compense atraso pulando etapas de segurança.'),
    ('Treinamento e autorização antes de novas tarefas','Procedimentos operacionais','Não execute atividade para a qual não recebeu orientação ou autorização. Peça demonstração, esclareça dúvidas e confirme os riscos e recursos antes de iniciar.'),
    ('Qualidade da limpeza sem atalhos inseguros','Qualidade e segurança','Siga sequência, diluição, tempo de contato e inspeção final. Não misture produtos, retire sinalização antes da hora ou omita EPI para ganhar tempo.'),
    ('Prevenção de danos a equipamentos e instalações','Qualidade e segurança','Use produtos e utensílios compatíveis com a superfície, não improvise ferramentas e informe danos imediatamente. Proteja pessoas e patrimônio durante toda a tarefa.'),
    ('Atendimento respeitoso a passageiros e visitantes','Conduta profissional','Mantenha comunicação cordial, preserve a privacidade e não discuta durante situações de tensão. Encaminhe pedidos, reclamações e emergências ao setor responsável.'),
    ('Acessibilidade e circulação de pessoas com deficiência','Circulação segura','Mantenha rotas acessíveis livres, não mova dispositivos de apoio sem autorização e posicione sinalizações sem bloquear passagens. Ofereça ajuda respeitando a autonomia da pessoa.'),
    ('Prevenção de riscos em dias de chuva','Prevenção de acidentes','Redobre a atenção em entradas e pisos úmidos, aumente a sinalização conforme procedimento e mantenha tapetes e passagens sob controle. Comunique infiltrações e pontos de alagamento.'),
    ('Iluminação adequada e identificação de perigos','Inspeção de segurança','Informe lâmpadas queimadas, áreas escuras e sombras que impeçam perceber obstáculos. Não realize tarefas de risco sem iluminação suficiente e sem autorização para corrigir a falha.'),
    ('Ventilação e uso seguro de produtos em ambientes fechados','Produtos químicos','Use produtos somente em locais e condições autorizados, respeite ventilação e rótulo e não misture substâncias. Interrompa a tarefa e comunique irritação ou odor inesperado.'),
    ('Proteção contra ruído em áreas operacionais','Saúde ocupacional','Observe sinalizações e utilize proteção auditiva quando indicada. Mantenha atenção aos alertas operacionais e comunique ruído anormal ou falha de comunicação.'),
    ('Prevenção de acidentes com portas automáticas','Circulação segura','Mantenha distância de partes móveis, não bloqueie sensores e não tente reparar portas. Sinalize falhas e informe a equipe responsável antes de continuar a atividade na área.'),
    ('Uso seguro de compactadores e equipamentos de resíduos','Equipamentos e ferramentas','Opere apenas se treinado e autorizado, nunca introduza mãos ou objetos em partes móveis e siga bloqueio e procedimento local. Comunique ruído, travamento ou falha.'),
    ('Movimentação e armazenamento seguro de materiais','Movimentação de materiais','Empilhe de forma estável, respeite limites e rotas, mantenha materiais pesados em posições seguras e não bloqueie equipamentos de emergência ou corredores.'),
    ('Prevenção de esmagamento de dedos e mãos','Prevenção de acidentes','Identifique pontos de prensamento em portas, carrinhos e equipamentos. Mantenha mãos fora das zonas de fechamento e não tente segurar cargas instáveis.'),
    ('Prevenção de cortes ao manusear embalagens','Prevenção de acidentes','Use ferramentas autorizadas e adequadas, corte afastando a lâmina do corpo e guarde-a com proteção. Não utilize lâminas improvisadas nem deixe objetos cortantes em resíduos comuns.'),
    ('Primeiros passos após um acidente de trabalho','Emergência','Proteja o local sem se colocar em risco, acione os canais de emergência e não mova a vítima salvo perigo imediato ou orientação de equipe treinada. Informe os fatos com clareza.'),
    ('Comunicação e registro de acidente de trabalho','Comunicação e prevenção','Comunique a ocorrência imediatamente conforme o fluxo interno, preserve informações e coopere com a investigação. O registro correto permite atendimento e prevenção de novos acidentes.'),
    ('Proteção de dados e confidencialidade no ambiente de trabalho','Conduta profissional','Acesse e compartilhe informações apenas quando necessário e autorizado. Não fotografe, publique ou divulgue dados de passageiros, colegas, crachás ou ocorrências sem permissão.'),
    ('Segurança durante atividades em horários de baixo fluxo','Procedimentos operacionais','Mantenha comunicação com a equipe, siga regras de acesso e não trabalhe isolado quando o procedimento exigir apoio. Informe mudanças de rota, falhas e condições inesperadas.'),
    ('Prevenção de riscos durante trabalho isolado','Gestão de riscos','Siga a regra local sobre atividades individuais, mantenha meios de comunicação disponíveis e informe localização e conclusão da tarefa quando exigido. Interrompa se perder condições de contato seguro.'),
    ('Mudança de tarefa: reavaliar riscos antes de continuar','Gestão de riscos','Quando mudarem o produto, equipamento, local ou condição de circulação, pare e reavalie os perigos. Confirme autorização, treinamento e medidas de controle antes de retomar.'),
    ('Segurança em tarefas não rotineiras','Gestão de riscos','Não presuma que a rotina habitual cobre uma atividade diferente. Solicite avaliação, instruções e autorização, e aguarde recursos e proteção adequados antes de começar.'),
    ('NR-01: responsabilidades e prevenção de riscos','Normas regulamentadoras','Conheça as orientações aplicáveis à sua função, siga as medidas de prevenção e comunique perigos. Consulte a versão vigente da NR-01 e os procedimentos oficiais da empresa.'),
    ('NR-05: CIPA e prevenção de acidentes e assédio','Normas regulamentadoras','Saiba como acionar os representantes e canais internos de prevenção. Compartilhe riscos e sugestões de melhoria e consulte as regras vigentes da CIPA e da organização.'),
    ('NR-06: uso e conservação de EPI','Normas regulamentadoras','Utilize EPI adequado e fornecido para a atividade, mantenha-o em condições de uso e comunique necessidade de troca. A orientação local e a versão vigente da NR-06 devem ser observadas.'),
    ('NR-07: saúde ocupacional e exames previstos','Normas regulamentadoras','Participe das ações de saúde ocupacional aplicáveis, compareça aos exames convocados e informe exposições e sintomas pelos canais corretos, preservando a confidencialidade médica.'),
    ('NR-09: exposição a agentes físicos, químicos e biológicos','Normas regulamentadoras','Reconheça fontes de exposição e siga controles definidos para ruído, calor, produtos e agentes biológicos. Não altere medidas preventivas e comunique condições diferentes da avaliação.'),
    ('NR-10: segurança em instalações elétricas','Normas regulamentadoras','Não intervenha em instalações elétricas sem autorização e capacitação. Mantenha equipamentos longe da água e comunique cabos, tomadas ou dispositivos danificados conforme o procedimento.'),
    ('NR-11: transporte e movimentação de materiais','Normas regulamentadoras','Respeite capacidade de carrinhos e equipamentos, mantenha cargas estáveis e rotas desobstruídas e opere somente equipamentos para os quais esteja autorizado e treinado.'),
    ('NR-12: segurança no uso de máquinas e equipamentos','Normas regulamentadoras','Não retire proteções nem faça ajustes com partes móveis em funcionamento. Siga instruções, utilize dispositivos de segurança e comunique falhas antes de liberar o equipamento.'),
    ('NR-17: ergonomia e adaptação do trabalho','Normas regulamentadoras','Use técnicas e recursos que reduzam esforço excessivo, organize a tarefa e comunique desconfortos. Siga as orientações ergonômicas e os procedimentos aplicáveis à função.'),
    ('NR-23: prevenção e proteção contra incêndios','Normas regulamentadoras','Conheça os procedimentos de emergência, mantenha rotas e equipamentos desobstruídos e participe dos treinamentos previstos. Siga as regras vigentes e as orientações específicas da unidade.'),
    ('NR-24: higiene e conforto nos locais de trabalho','Normas regulamentadoras','Preserve as condições de higiene, utilize corretamente instalações e áreas de apoio e comunique falta de água, insumos, limpeza ou condições que comprometam saúde e segurança.'),
    ('NR-26: sinalização de segurança','Normas regulamentadoras','Respeite placas, cores, rótulos e avisos de perigo. Não remova nem cubra sinalização sem autorização e comunique sinais danificados, ilegíveis ou ausentes.'),
    ('NR-32: prevenção de riscos em serviços de saúde','Normas regulamentadoras','Quando a atividade ocorrer em ambiente de saúde, siga os protocolos locais de risco biológico, resíduos, higiene e EPI. Não aplique orientações genéricas no lugar do protocolo do estabelecimento.'),
    ('NR-35: prevenção de acidentes em trabalho em altura','Normas regulamentadoras','Atividade em altura exige capacitação, autorização, análise de risco e sistemas de proteção aplicáveis. Não improvise acessos e siga integralmente o procedimento vigente.'),
    ('NR-38: segurança na limpeza e manejo de resíduos','Normas regulamentadoras','Aplique apenas os requisitos pertinentes à atividade e ao local, seguindo procedimentos de coleta, movimentação, higiene e proteção. Consulte o texto vigente da norma e a orientação técnica responsável.'),
    ('Riscos psicossociais: respeito, carga de trabalho e apoio','Saúde ocupacional','Comunique sobrecarga, conflitos, falta de recursos e situações de assédio pelos canais internos. A prevenção exige organização do trabalho, diálogo e medidas institucionais, não apenas esforço individual.'),
    ('Segurança no uso de ferramentas manuais','Equipamentos e ferramentas','Inspecione cabos, lâminas e partes móveis, use a ferramenta correta e não improvise extensões. Guarde-a após o uso e retire de serviço itens danificados.'),
    ('Prevenção de acidentes no deslocamento casa-trabalho','Segurança no trânsito','Planeje o trajeto, respeite sinalização e travessias, evite distrações e utilize equipamentos de proteção exigidos para o meio de transporte. Não conduza sob efeito de substâncias que reduzam a atenção.'),
    ('Segurança ao utilizar transporte interno e áreas de embarque','Circulação segura','Siga rotas e regras locais, aguarde em áreas autorizadas e mantenha distância de veículos e equipamentos em movimento. Não atravesse zonas operacionais por atalhos.'),
    ('Prevenção de escorregões causados por resíduos no piso','Organização e limpeza','Remova resíduos conforme o procedimento, mantenha a área sinalizada e confira o piso após a limpeza. Comunique vazamentos, gordura, cabos e obstáculos que possam causar queda.'),
    ('Inspeção final da área após a limpeza','Qualidade e segurança','Antes de liberar a área, confira piso seco ou seguro, retirada de resíduos, materiais guardados, sinalização conforme necessário e rotas desobstruídas. Informe qualquer pendência à liderança.'),
    ('Controle de estoque e identificação de produtos','Produtos químicos','Confira rótulos, validade quando aplicável, integridade e condições de armazenamento. Separe embalagens danificadas e não use produtos sem identificação ou fora do procedimento autorizado.'),
    ('Prevenção de contaminação ao recolher resíduos de sanitários','Riscos biológicos','Use os EPI e recipientes previstos, não pressione sacos nem misture resíduos incompatíveis. Higienize as mãos e os materiais conforme o procedimento após concluir a tarefa.'),
    ('Proteção de superfícies e equipamentos eletrônicos durante a limpeza','Limpeza aeroportuária','Evite borrifar líquidos diretamente em equipamentos, use métodos e produtos autorizados e mantenha líquidos longe de tomadas e conexões. Comunique qualquer derramamento ou dano.'),
    ('Limpeza segura em áreas com grande circulação de pessoas','Limpeza aeroportuária','Planeje horários e etapas com a liderança, sinalize a área e mantenha caminhos alternativos acessíveis. Pare se não for possível separar a tarefa do fluxo de pessoas.'),
    ('Prevenção de acidentes em depósitos e almoxarifados','Organização e limpeza','Mantenha corredores livres, empilhe materiais de forma estável e identifique produtos. Não suba em prateleiras nem bloqueie extintores, quadros elétricos ou saídas.'),
    ('Uso seguro de escovas, lâminas e acessórios de limpeza','Equipamentos e ferramentas','Confira compatibilidade do acessório, fixe-o corretamente e desligue o equipamento antes de trocar peças. Não utilize acessórios quebrados ou improvisados.'),
    ('Prevenção de riscos em áreas externas e rampas','Prevenção de acidentes','Observe inclinação, drenagem, condições do piso e circulação de veículos. Use rotas autorizadas, sinalize riscos e não transporte cargas que comprometam o equilíbrio.'),
    ('Trabalho seguro sob chuva e piso escorregadio','Prevenção de acidentes','Reduza a velocidade, use calçado adequado, sinalize áreas molhadas e não utilize equipamentos elétricos em condições inseguras. Comunique alagamentos e infiltrações.'),
    ('Gestão de pausas e recuperação durante a jornada','Saúde ocupacional','Respeite pausas e organização de trabalho previstas, hidrate-se conforme orientação e comunique fadiga ou dor. Não compense metas omitindo pausas de segurança.'),
    ('Aprendizado com ocorrências e melhoria contínua','Cultura de segurança','Compartilhe lições aprendidas sem expor indevidamente pessoas. Transforme relatos em ações, responsáveis e verificações, e confirme se as medidas reduziram o risco.'),
    ('Segurança como responsabilidade compartilhada','Cultura de segurança','Cada pessoa deve seguir procedimentos, usar corretamente os recursos e comunicar perigos; a liderança deve avaliar relatos e garantir condições adequadas para o trabalho seguro.')
]

def topic_content(topic: str, category: str, guidance: str) -> str:
    """Curated offline DDS copy with context for professional airport cleaning.

    Text is an editable draft. Site-specific SOPs, product labels/FDS and local
    response plans override generic guidance and must be checked by the leader.
    """
    t = normalize(topic)
    if any(key in t for key in ('DERRAMAMENTO', 'ORIGEM DESCONHECIDA', 'SUBSTANCIA DESCONHECIDA')):
        return ('RISCO E CENÁRIO\nUma substância desconhecida pode causar contato químico, vapores perigosos ou reação inesperada em áreas com passageiros e equipes.\n\n'
                'OBJETIVO\nEvitar exposição e impedir que pessoas entrem na área afetada.\n\n'
                'CONDUTA SEGURA\nNão toque, não cheire de perto e não misture produtos para tentar neutralizar. Afaste pessoas com segurança, sinalize/isole se isso não gerar exposição e acione imediatamente a liderança e a equipe de resposta definida no aeroporto. Não retome a limpeza sem orientação e liberação.\n\n'
                'MENSAGEM FINAL\nEm derramamento desconhecido, proteger e comunicar vem antes de limpar.')
    if any(key in t for key in ('CONTAMINACAO CRUZADA', 'CRUZADA NA LIMPEZA')):
        return ('RISCO E CENÁRIO\nPanos, mops, luvas ou carrinhos usados sem controle podem transportar sujidade entre sanitários, áreas de alimentação e superfícies de contato.\n\n'
                'OBJETIVO\nEvitar a transferência de contaminantes entre ambientes e manter a qualidade da limpeza.\n\n'
                'CONDUTA SEGURA\nSiga a sequência definida no procedimento local; use utensílios separados conforme a identificação adotada pela empresa; higienize ou substitua materiais no momento indicado; troque luvas quando necessário e higienize as mãos. Não leve materiais de sanitários para outras áreas.\n\n'
                'MENSAGEM FINAL\nSeparar materiais e respeitar a sequência evita espalhar a contaminação.')
    if any(key in t for key in ('SANITARIOS', 'BANHEIROS', 'ALTO FLUXO')):
        return ('RISCO E CENÁRIO\nSanitários aeroportuários têm uso intenso, superfícies diversas e circulação contínua. Pressa, isolamento insuficiente ou utensílios compartilhados podem aumentar exposição e risco de queda.\n\n'
                'OBJETIVO\nExecutar a limpeza com controle de circulação, higiene e qualidade.\n\n'
                'CONDUTA SEGURA\nSiga o procedimento e a sequência de limpeza; use produto regularizado e indicado para a finalidade; respeite diluição e tempo de contato do rótulo; mantenha sinalização até o piso estar seguro; não misture saneantes; reponha insumos e comunique avarias ou falta de material.\n\n'
                'MENSAGEM FINAL\nQualidade exige método, sinalização e conferência final.')
    if any(key in t for key in ('EMBARQUE', 'CIRCULACAO', 'PASSAGEIROS', 'AEROPORTUARIA', 'AREAS OPERACIONAIS')):
        return ('RISCO E CENÁRIO\nA limpeza ocorre junto a passageiros, bagagens, carrinhos e, em áreas restritas, veículos e equipamentos. Uma intervenção mal posicionada pode obstruir rotas ou expor pessoas.\n\n'
                'OBJETIVO\nManter a área limpa sem criar obstáculo ou acessar zona sem autorização.\n\n'
                'CONDUTA SEGURA\nPlaneje a tarefa; mantenha saídas, rotas acessíveis e equipamentos de emergência desobstruídos; sinalize e isole conforme o procedimento; conduza carrinhos sem bloquear a visão; respeite barreiras, autorizações e rotas locais. Se o fluxo impedir a execução segura, pare e combine a intervenção com a liderança.\n\n'
                'MENSAGEM FINAL\nA limpeza deve proteger pessoas e preservar a circulação segura.')
    if any(key in t for key in ('QUIMIC', 'SANEANTE', 'PRODUTO', 'DILUICAO', 'MISTUR')):
        return ('RISCO E CENÁRIO\nSaneantes incompatíveis, diluição incorreta e embalagens sem identificação podem causar queimaduras, irritação, intoxicação ou reação química.\n\n'
                'OBJETIVO\nPreparar e aplicar somente o produto correto, da forma autorizada.\n\n'
                'CONDUTA SEGURA\nConfira rótulo, finalidade, diluição, tempo de contato e EPI indicado; consulte a FDS quando disponível; use apenas recipientes identificados e utensílios previstos. Nunca misture produtos por iniciativa própria nem aumente a concentração para acelerar a limpeza. Em vazamento ou exposição, siga o plano local e comunique imediatamente.\n\n'
                'MENSAGEM FINAL\nRótulo, FDS e procedimento orientam a utilização segura.')
    return (f'RISCO E CENÁRIO\nNa limpeza profissional em aeroporto, a equipe atua perto de passageiros, bagagens, carrinhos e fluxos de trabalho contínuos. Falhas no tema “{topic}” podem causar exposição, queda, dano ou interrupção da operação.\n\n'
            f'OBJETIVO\nReforçar {category.lower()} com atenção ao risco real da tarefa e ao procedimento local.\n\n'
            f'CONDUTA SEGURA\n{guidance} Antes de iniciar, confira a área e os recursos necessários; preserve rotas acessíveis, saídas e equipamentos de emergência; utilize somente materiais autorizados e comunique condições inseguras à liderança. Se o risco não estiver controlado, interrompa a tarefa com segurança e peça orientação.\n\n'
            'MENSAGEM FINAL\nSiga o procedimento, proteja quem circula e não improvise diante de risco desconhecido.')


class _SearchResultParser(HTMLParser):
    """Small standard-library parser for DuckDuckGo HTML results."""
    def __init__(self):
        super().__init__(); self.results=[]; self._a=None; self._snippet=False; self._text=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        classes=attrs.get('class','')
        if tag=='a' and ('result__a' in classes or 'result-link' in classes):
            self._a={'url':attrs.get('href',''),'title':''}; self._text=[]
        if 'result__snippet' in classes or 'result-snippet' in classes:
            self._snippet=True; self._snippet_text=[]
    def handle_data(self, data):
        if self._a is not None: self._text.append(data)
        if self._snippet: self._snippet_text.append(data)
    def handle_endtag(self, tag):
        if tag=='a' and self._a is not None:
            self._a['title']=' '.join(' '.join(self._text).split())
            self.results.append(self._a); self._a=None; self._text=[]
        if tag in ('a','div','td') and self._snippet:
            self._snippet=False
            snippet=' '.join(' '.join(getattr(self,'_snippet_text',[])).split())
            if snippet and self.results: self.results[-1]['snippet']=snippet


def _approved_source_url(url: str) -> bool:
    """Allow public HTTPS result pages while blocking local/private and search-engine URLs."""
    try:
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname:
            return False
        host = parsed.hostname.lower().rstrip('.')
        if host in {'localhost', '127.0.0.1', '::1', '0.0.0.0'}:
            return False
        if host.endswith(('.local', '.internal', '.localhost')):
            return False
        # Search engines are discovery tools, not source documents to cite as evidence.
        search_hosts=('google.com','bing.com','duckduckgo.com','brave.com','ecosia.org','yahoo.com')
        if any(host == domain or host.endswith('.' + domain) for domain in search_hosts):
            return False
        # Reject literal private/reserved IP addresses.
        import ipaddress
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                return False
        except ValueError:
            pass
        return True
    except Exception:
        return False


def _source_is_official(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower().rstrip('.')
    path = parsed.path.lower()
    if host == 'fundacentro.gov.br' or host.endswith('.fundacentro.gov.br'):
        return True
    if host == 'gov.br' or host.endswith('.gov.br'):
        return any(x in path for x in ('/trabalho-e-emprego/', '/fundacentro/', '/anvisa/'))
    return False


def _fetch_text(url: str, timeout: float=2.5) -> str:
    parsed=urlparse(url)
    if not _approved_source_url(url): return ''
    req=Request(url,headers={'User-Agent':'GeradorDDS/1.1.6 (public safety research; contact local operator)'})
    with urlopen(req,timeout=timeout) as response:
        ctype=response.headers.get('Content-Type','').lower()
        if 'text/html' not in ctype and 'text/plain' not in ctype: return ''
        raw=response.read(900_000)
        charset=response.headers.get_content_charset() or 'utf-8'
    text=raw.decode(charset,errors='replace')
    # Drop scripts/styles and turn structural tags into spaces before stripping tags.
    text=re.sub(r'(?is)<(script|style|noscript|svg).*?>.*?</\1>', ' ', text)
    text=re.sub(r'(?i)</(p|li|h[1-6]|br|div|tr|section|article)>', '. ', text)
    text=re.sub(r'<[^>]+>', ' ', text)
    text=html.unescape(text)
    return re.sub(r'\s+',' ',text).strip()[:24000]

class _BingResultParser(HTMLParser):
    """Extracts result links and snippets from Bing's public HTML results."""
    def __init__(self):
        super().__init__(); self.results=[]; self._in_result=False; self._in_title=False; self._in_snippet=False; self._current=None; self._buf=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs); cls=a.get('class','')
        if tag=='li' and 'b_algo' in cls:
            self._in_result=True; self._current={'url':'','title':'','snippet':''}; self.results.append(self._current)
        elif self._in_result and tag=='a' and not self._current.get('url') and a.get('href','').startswith('http'):
            self._current['url']=a.get('href',''); self._in_title=True; self._buf=[]
        elif self._in_result and tag=='p': self._in_snippet=True; self._buf=[]
    def handle_data(self,data):
        if self._in_title or self._in_snippet: self._buf.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self._in_title:
            self._current['title']=' '.join(' '.join(self._buf).split()); self._in_title=False; self._buf=[]
        elif tag=='p' and self._in_snippet:
            self._current['snippet']=' '.join(' '.join(self._buf).split()); self._in_snippet=False; self._buf=[]
        elif tag=='li' and self._in_result: self._in_result=False; self._current=None


class _GoogleResultParser(HTMLParser):
    """Fallback parser for public Google HTML result links."""
    def __init__(self):
        super().__init__(); self.results=[]; self._link=None; self._buf=[]
    def handle_starttag(self, tag, attrs):
        if tag != 'a': return
        attrs=dict(attrs); href=attrs.get('href','')
        if href.startswith('http') or href.startswith('/url?'):
            self._link={'url':href,'title':''}; self._buf=[]
    def handle_data(self, data):
        if self._link is not None: self._buf.append(data)
    def handle_endtag(self, tag):
        if tag=='a' and self._link is not None:
            self._link['title']=' '.join(' '.join(self._buf).split())
            if self._link['title']: self.results.append(self._link)
            self._link=None; self._buf=[]


def _search_engine(query: str, engine: str, timeout: float=4.0) -> list[dict]:
    """Fetch a small public search result page with a strict timeout."""
    if engine == 'duckduckgo':
        url='https://html.duckduckgo.com/html/?q='+quote_plus(query)
        parser_cls=_SearchResultParser
    elif engine == 'google':
        url='https://www.google.com/search?gbv=1&num=10&hl=pt-BR&q='+quote_plus(query)
        parser_cls=_GoogleResultParser
    elif engine == 'bing':
        url='https://www.bing.com/search?q='+quote_plus(query)+'&count=10'
        parser_cls=_BingResultParser
    elif engine == 'brave':
        url='https://search.brave.com/search?q='+quote_plus(query)+'&source=web'
        parser_cls=_GoogleResultParser
    elif engine == 'ecosia':
        url='https://www.ecosia.org/search?q='+quote_plus(query)
        parser_cls=_GoogleResultParser
    elif engine == 'yahoo':
        url='https://search.yahoo.com/search?p='+quote_plus(query)
        parser_cls=_GoogleResultParser
    else:
        raise ValueError('Buscador não suportado')
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36','Accept-Language':'pt-BR,pt;q=0.9,en;q=0.8'})
    with urlopen(req,timeout=max(0.5,timeout)) as response:
        page=response.read(1_000_000).decode('utf-8',errors='replace')
    parser=parser_cls(); parser.feed(page)
    found=[]; seen=set()
    from urllib.parse import parse_qs
    search_hosts=('google.com','bing.com','duckduckgo.com','brave.com','ecosia.org','ecosia.com','yahoo.com')
    for item in parser.results:
        u=html.unescape(item.get('url','')).strip()
        if u.startswith('//'): u='https:'+u
        parsed=urlparse(u)
        if parsed.path == '/url' and parsed.netloc.lower().endswith('google.com'):
            params=parse_qs(parsed.query); u=params.get('q',params.get('url',['']))[0]
        if parsed.netloc.lower().endswith('duckduckgo.com'):
            target=parse_qs(parsed.query).get('uddg',[''])[0]
            if target: u=target
        # Remove common search-engine redirect wrappers when they expose a target URL.
        parsed=urlparse(u)
        if parsed.netloc.lower().endswith('yahoo.com'):
            params=parse_qs(parsed.query)
            for key in ('RU','url','u'):
                target=params.get(key,[''])[0]
                if target.startswith('https://'):
                    u=target
                    break
        u=unquote(u)
        host=(urlparse(u).hostname or '').lower().rstrip('.')
        if any(host == domain or host.endswith('.'+domain) for domain in search_hosts):
            continue
        if not _approved_source_url(u) or u in seen: continue
        seen.add(u)
        found.append({'title':item.get('title') or (urlparse(u).hostname or 'Fonte da internet'),'url':u,'snippet':item.get('snippet','')})
        if len(found)>=8: break
    return found


def _search_searxng(query: str, timeout: float=2.5) -> list[dict]:
    """Try public SearXNG JSON endpoints before the traditional HTML engines.

    DDS_SEARXNG_URL may contain comma-separated instance URLs. Public instances
    vary in availability and may disable JSON, so failures fall through safely.
    """
    configured=os.environ.get('DDS_SEARXNG_URL','').strip()
    bases=[x.strip().rstrip('/') for x in configured.split(',') if x.strip()]
    if not bases:
        bases=['https://searx.tiekoetter.com','https://search.sapti.me','https://searx.be','https://priv.au']
    for base in bases[:4]:
        if not base.startswith('https://'):
            continue
        try:
            endpoint=base if base.endswith('/search') else base+'/search'
            url=endpoint+'?q='+quote_plus(query)+'&format=json&language=pt-BR&safesearch=1'
            req=Request(url,headers={
                'User-Agent':'Mozilla/5.0 (compatible; GeradorDDS/1.2; public safety research)',
                'Accept':'application/json',
                'Accept-Language':'pt-BR,pt;q=0.9,en;q=0.8',
            })
            with urlopen(req,timeout=max(0.5,min(2.5,timeout))) as response:
                payload=json.loads(response.read(1_000_000).decode('utf-8',errors='replace'))
            found=[]
            for item in (payload.get('results',[]) if isinstance(payload,dict) else []):
                target=html.unescape(str(item.get('url','') or '')).strip()
                if not _approved_source_url(target):
                    continue
                host=(urlparse(target).hostname or '').lower().rstrip('.')
                if any(host==d or host.endswith('.'+d) for d in (
                    'searx.tiekoetter.com','search.sapti.me','searx.be','priv.au',
                    'google.com','bing.com','duckduckgo.com','brave.com','ecosia.org','yahoo.com'
                )):
                    continue
                title=' '.join(str(item.get('title','') or '').split()).strip()
                snippet=' '.join(str(item.get('content','') or item.get('snippet','') or '').split()).strip()
                if not title:
                    title=urlparse(target).hostname or 'Fonte da internet'
                found.append({'title':title,'url':target,'snippet':snippet})
                if len(found)>=8:
                    break
            if found:
                return found
        except Exception:
            # An instance may be down, rate-limited or have JSON disabled.
            continue
    return []


def _brave_api_search(query: str, timeout: float=5.0) -> list[dict]:
    """Optional server-side search provider. Never send credentials to browsers."""
    token=os.environ.get('DDS_BRAVE_API_KEY','').strip()
    if not token: return []
    endpoint='https://api.search.brave.com/res/v1/web/search?q='+quote_plus(query)+'&count=6'
    req=Request(endpoint,headers={'Accept':'application/json','X-Subscription-Token':token})
    with urlopen(req,timeout=timeout) as response:
        payload=json.loads(response.read(500000).decode('utf-8'))
    results=[]
    for item in (payload.get('web',{}).get('results',[]) if isinstance(payload,dict) else []):
        target=str(item.get('url') or '')
        if _approved_source_url(target):
            results.append({'title':str(item.get('title') or target),
                            'url':target,'snippet':str(item.get('description') or '')})
    return results


def _saved_topic_for_reuse(title: str) -> dict | None:
    """Only reuse exactly matching locally saved material; no claimed fresh verification."""
    for row in topic_rows():
        if normalize(row.get('title')) == normalize(title):
            return row
    return None


def _web_search(topic: str) -> list[dict]:
    """Find and read actual public source documents; never treat search snippets as evidence."""
    clean_topic=' '.join(str(topic or '').split()).strip()
    normalized=normalize(clean_topic)
    detail=re.sub(r'\bDDS\b', '', clean_topic, flags=re.IGNORECASE).strip(' :-–—')
    subject=detail or clean_topic
    queries=[
        f'{subject} riscos prevenção site:gov.br/trabalho-e-emprego',
        f'{subject} segurança saúde site:gov.br/fundacentro',
        f'{subject} prevenção segurança do trabalho Brasil',
    ]
    if any(term in normalized for term in ('QUIMIC', 'SANITIZ', 'SANEANTE', 'DESINFEC', 'LIMPEZA')):
        queries.insert(1, f'{subject} saneantes segurança site:gov.br/anvisa')
    deadline=time.monotonic()+24.0
    engines=('brave-api','searxng','brave','ecosia','duckduckgo','google','bing')
    seen=set()
    verified=[]
    for index,engine in enumerate(engines):
        remaining=deadline-time.monotonic()
        if remaining<2.0 or len(verified)>=3: break
        try:
            query=queries[index % len(queries)]
            found=(_brave_api_search(query,timeout=min(3.0,remaining)) if engine=='brave-api' else
                   _search_searxng(query,timeout=min(2.5,remaining)) if engine=='searxng' else
                   _search_engine(query,engine,timeout=min(3.0,remaining)))
        except Exception as exc:
            app.logger.info('Buscador DDS %s indisponível: %s',engine,type(exc).__name__)
            continue
        found.sort(key=lambda item: (not _source_is_official(item.get('url','')), item.get('url','')))
        for item in found[:5]:
            url=item.get('url','')
            if url in seen or not _approved_source_url(url): continue
            seen.add(url)
            remaining=deadline-time.monotonic()
            if remaining<1.0: break
            try:
                body=_fetch_text(url,timeout=min(3.0,remaining))
                excerpt=_extract_relevant_sentences(body,subject,limit=4)
            except Exception:
                continue
            if not excerpt: continue
            # Only a successfully read original document can back a generated DDS.
            verified.append({
                'title':str(item.get('title') or url)[:200], 'url':url,
                'excerpt':excerpt[:1250],
                'authority':'Fonte oficial' if _source_is_official(url) else 'Fonte complementar — conferir antes de usar'
            })
            if len(verified)>=3: break
    if not verified:
        raise RuntimeError('Nenhuma fonte primária legível e pertinente encontrada nesta tentativa.')
    return verified


def _extract_relevant_sentences(text: str, topic: str, limit: int=5) -> str:
    if not text: return ''
    stop={'para','sobre','como','com','das','dos','uma','tema','trabalho','dds','diario','dialogo','seguranca'}
    terms=[x.lower() for x in re.findall(r'[A-Za-zÀ-ÿ0-9]{4,}',topic) if x.lower() not in stop]
    if not terms: return ''
    sentences=re.split(r'(?<=[.!?])\s+',text)
    ranked=[]
    for sent in sentences:
        sent=' '.join(sent.split()).strip(' .')
        if not 65<=len(sent)<=450: continue
        low=sent.lower()
        overlap=sum(1 for term in terms if term in low)
        if not overlap: continue
        safety=sum(1 for word in ('preven','risco','seguran','prote','trabalhador','orienta','exposi','saúde','acidente','procedimento') if word in low)
        ranked.append((overlap*4+safety,sent))
    ranked.sort(key=lambda x:(-x[0],len(x[1])))
    chosen=[]; seen=set()
    for _,sent in ranked:
        if sent.lower() in seen: continue
        seen.add(sent.lower());chosen.append(sent)
        if len(chosen)>=limit: break
    return ' '.join(chosen)[:1800]


def _draft_from_sources(topic: str, sources: list[dict]) -> str:
    """Produce a short attributable working draft, not unsupported invented instructions."""
    evidence=[]
    for item in sources:
        excerpt=str(item.get('excerpt','')).strip()
        if excerpt and _approved_source_url(item.get('url','')):
            evidence.append(excerpt)
    if not evidence: return ''
    # Bound the number of characters before preflighting the actual institutional PDF.
    sections=[
        f'TEMA: {topic}',
        'OBJETIVO\nReconhecer os perigos ligados ao tema e discutir medidas preventivas aplicáveis à nossa operação.',
        'ORIENTAÇÕES ENCONTRADAS NAS FONTES'
    ]
    budget=1000
    for index,excerpt in enumerate(evidence[:3],start=1):
        compact=' '.join(excerpt.split())
        if len(compact)>budget:compact=compact[:budget].rsplit(' ',1)[0].rstrip(' ,;')+'.'
        if not compact:continue
        sections.append(f'{index}. {compact}')
        budget-=len(compact)
        if budget<=90:break
    sections.append('APLICAÇÃO NO POSTO\nAntes da atividade, avalie os riscos reais, siga o POP e os controles aprovados, confira EPI e FDS quando aplicáveis e comunique condições inseguras à liderança. Adapte estas orientações ao local; não improvise procedimentos.')
    sections.append('MENSAGEM FINAL\nSegurança exige reconhecimento do risco, prevenção e comunicação.')
    return '\n\n'.join(sections)
    
def init_topics_db() -> None:
    with closing(sqlite3.connect(_topics_db_path())) as con:
        con.execute('''CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL COLLATE NOCASE UNIQUE,
            category TEXT NOT NULL, objective TEXT NOT NULL, content TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'local', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        now=datetime.now().isoformat(timespec='seconds')
        for title, category, guidance in TOPIC_SEEDS:
            objective=f'Reforçar práticas seguras relacionadas a {title.lower()}, identificar perigos e orientar a equipe sobre prevenção.'
            content=topic_content(title,category,guidance)
            con.execute('''INSERT OR IGNORE INTO topics(title,category,objective,content,source,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)''',(title,category,objective,content,'biblioteca inicial',now,now))
            # Refresh only original seed records. User-edited/custom topics remain untouched.
            con.execute('''UPDATE topics SET category=?,objective=?,content=?,updated_at=?
                WHERE title=? AND source='biblioteca inicial' AND (category<>? OR objective<>? OR content<>?) ''',(category,objective,content,now,title,category,objective,content))
        con.commit()

def topic_source_metadata(source_value: str) -> dict:
    """Expose provenance from the existing source column while preserving the DB schema."""
    source_value=str(source_value or '')
    if source_value.startswith('WEB-PESQUISADO:'):
        label='Pesquisa online — revisar'
        status='online-research'
        raw_sources=source_value.partition(':')[2]
    elif source_value.startswith('OFFLINE-DRAFT:'):
        label='Rascunho offline — não verificado'
        status='offline-draft'
        raw_sources=''
    elif source_value=='biblioteca inicial':
        label='Catálogo inicial — conferir antes de usar'
        status='initial-library'
        raw_sources=''
    elif source_value=='cadastrado pelo usuário':
        label='Cadastrado/editado manualmente'
        status='user-maintained'
        raw_sources=''
    else:
        label='Tema cadastrado'
        status='saved-topic'
        raw_sources=''
    sources=[]
    if raw_sources:
        try:
            parsed=json.loads(raw_sources)
        except (TypeError, ValueError):
            parsed=[]
        if isinstance(parsed,list):
            for item in parsed:
                if not isinstance(item,dict):
                    continue
                url=str(item.get('url') or '').strip()
                if not _approved_source_url(url):
                    continue
                sources.append({
                    'title':str(item.get('title') or url)[:240],
                    'url':url,
                    'authority':str(item.get('authority') or ''),
                    'snippet':str(item.get('excerpt') or item.get('snippet') or '')[:500],
                })
    return {'source_label':label,'source_status':status,'sources':sources}


def topic_rows(q: str = '', category: str = '') -> list[dict]:
    init_topics_db()
    sql='SELECT id,title,category,objective,content,source,created_at,updated_at FROM topics WHERE 1=1'
    args=[]
    if q.strip():
        sql+=' AND (title LIKE ? OR category LIKE ? OR content LIKE ?)'
        term='%'+q.strip()+'%'; args.extend([term,term,term])
    if category.strip() and category.strip()!='TODAS':
        sql+=' AND category=?'; args.append(category.strip())
    sql+=' ORDER BY category COLLATE NOCASE,title COLLATE NOCASE'
    with closing(sqlite3.connect(_topics_db_path())) as con:
        con.row_factory=sqlite3.Row
        rows=[dict(row) for row in con.execute(sql,args).fetchall()]
    for row in rows:
        row.update(topic_source_metadata(row.get('source','')))
    return rows

def read_state() -> dict:
    if not _state_path().exists(): return default_state()
    try:
        data=json.loads(_state_path().read_text(encoding='utf-8'))
        if not isinstance(data,dict): return default_state()
        data.setdefault('employees',[]); data.setdefault('history',[]); data.setdefault('settings',default_state()['settings'])
        # Version 1.1.3: approved operational units are GIG, TECA, UNITED and GATE GOURMET.
        # Preserve legacy work-site assignments through canonical_site aliases.
        migrated = False
        for employee in data['employees']:
            if not isinstance(employee, dict):
                continue
            old_sector = str(employee.get('sector') or '').strip()
            old_site = (canonical_site(employee.get('work_site'))
                        or canonical_site(employee.get('operational_unit'))
                        or canonical_site(old_sector)
                        or canonical_site(employee.get('unit')))
            if employee.get('work_site') != old_site:
                employee['work_site'] = old_site
                migrated = True
            resolved_unit = old_site or ''
            if employee.get('operational_unit') != resolved_unit:
                employee['operational_unit'] = resolved_unit
                migrated = True
            if employee.get('unit') != resolved_unit:
                employee['unit'] = resolved_unit
                migrated = True
            if employee.get('sector') != 'LIMPEZA':
                employee['sector'] = 'LIMPEZA'
                migrated = True
        for record in data['history']:
            if not isinstance(record, dict):
                continue
            old_history_unit = str(record.get('unit') or '').strip()
            old_history_site = canonical_site(record.get('work_site')) or canonical_site(old_history_unit)
            resolved_history_unit = old_history_site or UNIT_CODE
            if record.get('unit') != resolved_history_unit:
                record['unit'] = resolved_history_unit
                migrated = True
            if not old_history_site and normalize(old_history_unit) in ('TODAS AS UNIDADES', 'TODOS OS LOCAIS', 'TODOS OS SETORES', 'TODOS', ''):
                old_history_site = 'Todos os locais'
            if record.get('work_site') != old_history_site:
                record['work_site'] = old_history_site
                migrated = True
        if migrated:
            write_state(data)
        return data
    except Exception:
        # Never silently overwrite a corrupt state file. Keep a backup and recover safely.
        backup=_state_path().with_suffix('.corrupt.json')
        try: _state_path().replace(backup)
        except Exception: pass
        return default_state()


def write_state(state: dict) -> None:
    temp=_state_path().with_suffix('.tmp')
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(_state_path())


def clean_employee(raw: dict) -> dict:
    name=str(raw.get('name','')).strip()
    shift=normalize_shift(raw.get('shift',''))
    area='LIMPEZA'  # Official PDF requirement: AREA is always LIMPEZA.
    raw_sector=str(raw.get('sector','')).strip()
    work_site=(canonical_site(raw.get('work_site')) or canonical_site(raw.get('operational_unit'))
               or canonical_site(raw_sector) or canonical_site(raw.get('unit')))
    # Unit selection is one of GIG, TECA, UNITED or GATE GOURMET.
    unit=work_site
    operational_unit=work_site
    sector='LIMPEZA'
    return {'id':str(raw.get('id') or uuid.uuid4().hex[:12]),'name':name,'shift':shift,
            'area':area,'sector':sector,'unit':unit,'operational_unit':operational_unit,
            'work_site':work_site,'source_sheet':str(raw.get('source_sheet',''))}


def col_index(headers: list[str], candidates: list[str]) -> int | None:
    n=[normalize(h).replace(' ','_') for h in headers]
    # Prefer exact normalized headers, then useful substring matches.
    for c in candidates:
        key=normalize(c).replace(' ','_')
        if key in n: return n.index(key)
    for i,h in enumerate(n):
        if h and any(c in h for c in [normalize(x).replace(' ','_') for x in candidates]): return i
    return None


def detect_header(rows: list[tuple], max_scan=25) -> int | None:
    keys=['NOME','COLABORADOR','FUNCIONARIO','TURNO','ESCALA','SETOR','ÁREA','AREA','UNIDADE','POSTO','LOCAL']
    best=None; best_score=0
    for i,row in enumerate(rows[:max_scan]):
        vals=[normalize(v) for v in row if v is not None]
        score=sum(1 for v in vals if any(k in v for k in keys))
        if score>best_score and any(('NOME' in v or 'COLABORADOR' in v or 'FUNCIONARIO' in v) for v in vals):
            best=i; best_score=score
    return best


def find_shift_in_text(value: str) -> str:
    n=normalize(value)
    for key,sh in SHIFT_ALIASES.items():
        if key in n: return sh
    return ''


def canonical_site(value: Any) -> str:
    """Map imported site labels and aliases to the four approved work locations."""
    raw=str(value or '').strip()
    n=normalize(raw)
    if not n: return ''
    aliases={normalize(k):v for k,v in SITE_ALIASES.items()}
    aliases.update({normalize(x):x for x in WORK_SITES})
    if n in aliases: return aliases[n]
    for key,label in aliases.items():
        if n.startswith(key+' ') or n.startswith(key+'-') or n.startswith(key+'/'):
            return label
    return ''


def canonical_unit(value: Any) -> str:
    """Deprecated compatibility wrapper; older builds used this for work sites."""
    return canonical_site(value)

def canonical_sector(value: Any, known_sectors: dict[str,str] | None = None) -> str:
    raw=str(value or '').strip()
    n=normalize(raw)
    if not n: return ''
    if n == 'LIMPEZA': return 'LIMPEZA'
    if known_sectors:
        for key,label in known_sectors.items():
            if n==key or n.startswith(key+' ') or n.startswith(key+'-') or n.startswith(key+'/'):
                return label
    return raw


MONTH_TABS = {
    'JANEIRO': 1, 'FEVEREIRO': 2, 'MARCO': 3, 'ABRIL': 4,
    'MAIO': 5, 'JUNHO': 6, 'JULHO': 7, 'AGOSTO': 8,
    'SETEMBRO': 9, 'OUTUBRO': 10, 'NOVEMBRO': 11, 'DEZEMBRO': 12,
}


def select_roster_sheets(worksheets, current_month: int | None = None):
    """For monthly attendance workbooks, use one month's roster, not a merge of future/past shifts."""
    if current_month is None:
        current_month = datetime.now().month
    monthly = []
    for ws in worksheets:
        title = normalize(ws.title)
        for month_name, month_number in MONTH_TABS.items():
            if title == month_name or title.startswith(month_name + ' '):
                monthly.append((ws, month_number, month_name))
                break
    if not monthly:
        return list(worksheets), []
    for ws, month_number, month_name in monthly:
        if month_number == current_month:
            return [ws], [f'Planilha mensal: foi importada somente a aba “{ws.title}” (mês atual), para evitar misturar turnos de meses diferentes.']
    # If there is no tab for this month, use the most recent available month in the calendar cycle.
    ws, month_number, month_name = min(monthly, key=lambda item: (current_month - item[1]) % 12)
    return [ws], [f'ATENÇÃO: não há aba do mês atual. Foi selecionada a aba mensal mais recente disponível: “{ws.title}”. Confira se é o período desejado antes de gerar DDS.']


def parse_xlsx(file_storage, current_month: int | None = None) -> tuple[list[dict], list[str]]:
    wb=load_workbook(file_storage, read_only=True, data_only=True)
    selected_sheets, selection_notes = select_roster_sheets(wb.worksheets, current_month)
    monthly_import = any(
        normalize(ws.title) in MONTH_TABS or any(normalize(ws.title).startswith(month + ' ') for month in MONTH_TABS)
        for ws in selected_sheets
    )
    parsed=[]; notes=list(selection_notes)
    name_cols=['NOME','NOMES','NOME_COMPLETO','COLABORADOR','FUNCIONARIO','FUNCIONARIO_NOME','NOME DO COLABORADOR','EMPREGADO']
    shift_cols=['TURNO','SHIFT','ESCALA','PERIODO','PERÍODO','JORNADA']
    area_cols=['AREA','ÁREA','FUNCAO','FUNÇÃO','ATIVIDADE','CARGO','EQUIPE']
    sector_cols=['UNIDADE_OPERACIONAL','SETOR_OPERACIONAL','LOCAL_TRABALHO','POSTO','FRENTE','CLIENTE','LOCAL','SETOR','CONTRATO']
    unit_cols=['CODIGO_UNIDADE','COD_UNIDADE','BASE','SIGLA_UNIDADE','CODIGO','UNIDADE_GPS']
    for ws in selected_sheets:
        rows=list(ws.iter_rows(values_only=True))
        if not rows: continue
        header_idx=detect_header(rows)
        sheet_shift=find_shift_in_text(ws.title)
        if header_idx is None:
            # Accept simple lists: one name per row; sheet tab supplies the shift.
            if not sheet_shift:
                notes.append(f'Aba “{ws.title}”: não encontrei cabeçalho de nome nem turno no nome da aba; foi ignorada.')
                continue
            for row in rows:
                vals=[str(v).strip() for v in row if v is not None and str(v).strip()]
                if not vals: continue
                name=vals[0]
                if normalize(name) in ('NOME','COLABORADOR','FUNCIONARIO','FUNCIONARIOS','LISTA DE COLABORADORES'): continue
                employee = clean_employee({'name':name,'shift':sheet_shift,'area':'LIMPEZA','sector':'LIMPEZA','unit':'','operational_unit':'','work_site':'','source_sheet':ws.title})
                employee['_area_missing'] = True
                parsed.append(employee)
            notes.append(f'Aba “{ws.title}”: lista simples importada; confira a unidade de cada colaborador antes de gerar o DDS.')
            continue
        headers=[str(v or '').strip() for v in rows[header_idx]]
        nidx=col_index(headers,name_cols)
        if nidx is None:
            notes.append(f'Aba “{ws.title}”: cabeçalho identificado, mas não encontrei coluna de nome; foi ignorada.')
            continue
        sidx=col_index(headers,shift_cols)
        aidx=col_index(headers,area_cols)
        secidx=col_index(headers,sector_cols)
        uidx=col_index(headers,unit_cols)
        # Older sheets sometimes stored a work-site name under "Unidade";
        # recognise both site names and the fixed GIG unit code.
        unit_col=col_index(headers,['UNIDADE'])
        if unit_col is not None:
            vals=[r[unit_col] for r in rows[header_idx+1:] if len(r)>unit_col and r[unit_col] is not None]
            if any(canonical_site(v) for v in vals):
                if uidx is None: uidx=unit_col
            elif uidx is None:
                uidx=unit_col
        imported=0
        for row in rows[header_idx+1:]:
            if len(row)<=nidx or row[nidx] is None: continue
            name=str(row[nidx]).strip()
            if not name or normalize(name) in ('NOME','COLABORADOR','FUNCIONARIO'): continue
            shift=normalize_shift(row[sidx] if sidx is not None and len(row)>sidx else '') if sidx is not None else ''
            shift=shift or sheet_shift
            sector='LIMPEZA'
            work_site=''
            if secidx is not None and len(row)>secidx and row[secidx] is not None:
                rawsec=str(row[secidx]).strip()
                work_site=canonical_site(rawsec)
            area=str(row[aidx]).strip() if aidx is not None and len(row)>aidx and row[aidx] is not None else ''
            raw_unit=str(row[uidx]).strip() if uidx is not None and len(row)>uidx and row[uidx] is not None else ''
            unit_from_column=canonical_site(raw_unit)
            # Legacy workbooks sometimes have main unit GIG plus a specific site in another column.
            # Prefer a specific sub-unit over generic GIG; otherwise use the unit column.
            if unit_from_column and unit_from_column != 'GIG':
                work_site=unit_from_column
            elif work_site and work_site != 'GIG':
                pass
            else:
                work_site=unit_from_column or work_site
            employee = clean_employee({'name':name,'shift':shift,'area':'LIMPEZA','sector':sector,'unit':work_site,'operational_unit':work_site,'work_site':work_site,'source_sheet':ws.title})
            employee['_area_missing'] = not bool(area)
            parsed.append(employee)
            imported+=1
        if imported==0: notes.append(f'Aba “{ws.title}”: não encontrei registros para importar.')
    wb.close()
    # In a monthly roster, repeated rows with missing turno or function must not create a false second shift.
    # Keep fully specified assignments; retain genuine multi-shift entries only when each row is complete.
    if monthly_import:
        grouped = {}
        for employee in parsed:
            grouped.setdefault(normalize(employee['name']), []).append(employee)
        rejected_ids = set()
        for group in grouped.values():
            if len(group) < 2:
                continue
            complete = [e for e in group if e.get('shift') and not e.get('_area_missing')]
            if complete:
                for employee in group:
                    if employee not in complete and (not employee.get('shift') or employee.get('_area_missing')):
                        rejected_ids.add(id(employee))
        if rejected_ids:
            parsed = [e for e in parsed if id(e) not in rejected_ids]
            notes.append(f'{len(rejected_ids)} registro(s) repetido(s) incompleto(s) foram ignorados; foi mantido o registro com turno e função preenchidos.')
    for employee in parsed:
        employee.pop('_area_missing', None)
    # Deduplicate without losing the same name assigned to separate shifts or work sites.
    unique=[]; seen=set(); duplicate_count=0
    for e in parsed:
        k=(normalize(e['name']),e['shift'],normalize(e.get('work_site','')))
        if k not in seen: seen.add(k); unique.append(e)
        else: duplicate_count+=1
    if duplicate_count: notes.append(f'{duplicate_count} registro(s) duplicado(s) foram ignorados (mesmo nome, turno e local de trabalho).')
    return unique, notes


def wrap_words(text: str, fontname: str, fontsize: float, width: float) -> list[str]:
    """Wrap by measured font width, including a single token longer than the cell."""
    lines=[]; line=''

    def split_long_word(word: str) -> list[str]:
        parts=[]
        remainder=word
        while remainder:
            low,high,best=1,len(remainder),0
            while low<=high:
                mid=(low+high)//2
                measured=fitz.get_text_length(remainder[:mid],fontname=fontname,fontsize=fontsize)
                if measured<=width:
                    best=mid; low=mid+1
                else:
                    high=mid-1
            if best==0: best=1
            parts.append(remainder[:best]); remainder=remainder[best:]
        return parts

    for word in text.split():
        if fitz.get_text_length(word,fontname=fontname,fontsize=fontsize)>width:
            if line: lines.append(line); line=''
            pieces=split_long_word(word)
            lines.extend(pieces[:-1])
            line=pieces[-1] if pieces else ''
            continue
        candidate=(line+' '+word).strip()
        if line and fitz.get_text_length(candidate,fontname=fontname,fontsize=fontsize)>width:
            lines.append(line); line=word
        else:
            line=candidate
    if line: lines.append(line)
    return lines


def split_content(content: str) -> list[tuple[str,bool]]:
    entries=[]
    for raw in (content or '').replace('\r','').split('\n'):
        line=raw.strip()
        if not line:
            entries.append(('',False)); continue
        plain=line.lstrip('•-*–—0123456789. )')
        is_heading=(len(line)<=48 and (line.endswith(':') or line.upper()==line or plain.upper() in {'OBJETIVO','ORIENTAÇÕES','ORIENTACOES','REGRAS GERAIS','MENSAGEM FINAL','DESLOCAMENTO SEGURO'}))
        entries.append((line,is_heading))
    return entries


def draw_content(page, content: str, writer: fitz.TextWriter | None = None) -> bool:
    # Text is written only inside the original Objective/Content cell of the user's approved PDF.
    x0, x1 = 101.5, 566.0
    max_y = 299.5
    entries = split_content(content)
    for base in (8.1, 7.8, 7.5, 7.2, 6.9, 6.6, 6.3, 6.0):
        commands=[]; y=111.5; overflow=False
        for para,is_heading in entries:
            if para=='': y+=2.4; continue
            font='hebo' if is_heading else 'helv'
            size=(base+0.15) if is_heading else base
            for line in wrap_words(para,font,size,x1-x0-8.0):
                y += size*1.14
                if y > max_y:
                    overflow=True; break
                commands.append((x0,y,line,font,size))
            if overflow: break
            y += 0.8
        if not overflow:
            for x,y,line,font,size in commands:
                if writer is not None:
                    writer.append((x,y), line, font=fitz.Font(fontname=font), fontsize=size)
                else:
                    page.insert_text((x,y),line,fontname=font,fontsize=size,color=(0,0,0),overlay=True)
            return base < 8.1
    raise ValueError('O conteúdo ficou longo demais para o campo do formulário institucional. Resuma o texto antes de gerar o PDF.')


def validate_content_fits_pdf(content: str) -> None:
    """Preflight the current content cell without modifying the approved PDF template."""
    text_value=str(content or '').strip()
    if not text_value:
        raise ValueError('Informe o conteúdo do DDS antes de salvar o tema.')
    if len(text_value)>2600:
        raise ValueError('O conteúdo ultrapassa 2.600 caracteres. Revise o texto antes de salvar.')
    width,height=595.0,842.0
    if _active_template_path().exists():
        with fitz.open(_active_template_path()) as template:
            if not len(template):
                raise ValueError('O modelo institucional não contém uma página válida.')
            width,height=float(template[0].rect.width),float(template[0].rect.height)
    probe=fitz.open()
    try:
        scratch=probe.new_page(width=width,height=height)
        draw_content(scratch,text_value)
    finally:
        probe.close()


def _count_pdf_pages(path: Path) -> int:
    with fitz.open(path) as check_doc:
        return len(check_doc)


def tracked_centered_text(writer: fitz.TextWriter, text: str, x0: float, x1: float,
                          baseline: float, size: float = 7.0,
                          letter_spacing: float = 0.35) -> None:
    """Centraliza o nome com espaçamento discreto entre letras, sem desenhar fundo.

    Renderiza cada caractere na mesma linha, preservando a leitura do nome
    como texto selecionável no PDF.
    """
    text = str(text or '')
    if not text:
        return
    font = fitz.Font(fontname='helv')
    natural_width = fitz.get_text_length(text, fontname='helv', fontsize=size)
    total_width = natural_width + max(0, len(text) - 1) * letter_spacing
    if total_width > x1 - x0 - 4.0:
        raise ValueError('Nome do responsável excede o espaço da célula institucional.')
    x = x0 + (x1 - x0 - total_width) / 2.0
    for char in text:
        if not char.isspace():
            writer.append((x, baseline), char, font=font, fontsize=size)
        x += fitz.get_text_length(char, fontname='helv', fontsize=size) + letter_spacing


def centered_text(page, text: str, x0: float, x1: float, baseline: float, font='helv', size=8.0, min_size=6.0, color=(0,0,0), writer: fitz.TextWriter | None = None) -> float:
    text=str(text or '')
    cur=size
    while cur>=min_size:
        width=fitz.get_text_length(text,fontname=font,fontsize=cur)
        if width<=x1-x0-4: break
        cur-=0.25
    if cur<min_size:
        raise ValueError(f'Texto longo demais para o campo do formulário: {text[:70]}. Reduza o texto ou ajuste o cadastro antes de gerar o PDF.')
    width=fitz.get_text_length(text,fontname=font,fontsize=cur)
    x=x0+(x1-x0-width)/2
    if writer is not None and color == (0, 0, 0):
        writer.append((x,baseline), text, font=fitz.Font(fontname=font), fontsize=cur)
    else:
        page.insert_text((x,baseline),text,fontname=font,fontsize=cur,color=color,overlay=True)
    return cur


BLANK_ATTENDANCE_ROWS = 5
EXTENSION_SOURCE_PAGE = 2  # Institutional continuation page with 42 attendance rows.


def update_attendance_page_counter(page, page_number: int, total_pages: int) -> None:
    # The approved reference keeps page numbers only in the bottom-right footer.
    # The packaged clean template has its old footer values removed already.
    return None


def finalize_pdf_pagination_and_footer(doc, selected_unit: str, writers: dict[int, fitz.TextWriter] | None = None) -> None:
    """Add the mandatory notice and physical page counter in the original footer band."""
    total_pages = len(doc)
    for index, page in enumerate(doc, start=1):
        writer = writers.get(index - 1) if writers else None
        centered_text(page, 'PROIBIDA a reprodução não autorizada', 22.3, 412.6, 813.1,
                      font='helv', size=7.0, min_size=6.0, writer=writer)
        centered_text(page, f'Página {index} de {total_pages}', 492.0, 568.0, 813.1,
                      font='helv', size=7.0, min_size=5.8, writer=writer)


def prepare_attendance_pages(doc, layout: dict, required_rows: int) -> list[dict]:
    """Keep required attendance slots, adding continuation sheets in the exact supplied layout."""
    pages = [dict(info, rows=[dict(row) for row in info['rows']], extension=False)
             for info in layout['pages']]
    columns = layout['columns']
    base_capacity = sum(len(info['rows']) for info in pages)
    source_rows = [dict(row) for row in layout['pages'][EXTENSION_SOURCE_PAGE]['rows']]
    extension_capacity = len(source_rows)
    extra_page_count = max(0, math.ceil((required_rows - base_capacity) / extension_capacity))

    for extra_index in range(extra_page_count):
        doc.fullcopy_page(EXTENSION_SOURCE_PAGE)
        page_index = len(doc) - 1
        first_number = base_capacity + extra_index * extension_capacity + 1
        copied_rows = []
        for row_index, source_row in enumerate(source_rows):
            row = dict(source_row)
            row['number'] = first_number + row_index
            copied_rows.append(row)
        pages.append({'page': page_index, 'rows': copied_rows, 'extension': True})

    consumed = 0
    last_page_position = 0
    used_on_last_page = 0
    for pos, info in enumerate(pages):
        if required_rows <= consumed + len(info['rows']):
            last_page_position = pos
            used_on_last_page = required_rows - consumed
            break
        consumed += len(info['rows'])
    else:
        raise ValueError('Não foi possível preparar linhas suficientes para a lista de presença.')

    last_info = pages[last_page_position]
    if used_on_last_page < len(last_info['rows']):
        unused_rows = last_info['rows'][used_on_last_page:]
        first_unused = unused_rows[0]
        last_unused = unused_rows[-1]
        page = doc[last_info['page']]
        x0 = float(columns['number'][0])
        x1 = float(columns['signature'][1])
        y0 = float(first_unused['top'])
        y1 = float(last_unused['bottom'])
        # Ocultar somente o interior das linhas excedentes. A borda de cima
        # e a propria borda da ultima linha em uso: preservar seu traco duplo
        # original, sem substituir por linha artificial de 0,45 pt.
        # Ignorar graficos vetoriais na remocao impede apagar, por inteiro,
        # caminhos da tabela que atravessam a area recortada. O retangulo
        # branco cobre apenas a parte efetivamente excedente.
        trim_top = min(y0 + 1.8, y1)
        if trim_top < y1:
            page.add_redact_annot(
                fitz.Rect(x0, trim_top, x1, y1),
                fill=(1, 1, 1), cross_out=False
            )
            page.apply_redactions(images=0, graphics=0, text=0)

    final_page_index = last_info['page']
    if final_page_index < len(doc) - 1:
        doc.delete_pages(final_page_index + 1, len(doc) - 1)
    return pages[:last_page_position + 1]


# Todas as divisorias de presenca compartilham exatamente o mesmo traco.
# Os contornos do modelo continuam no arquivo institucional; nao e feita
# redacao, mascara branca ou redesenho das linhas horizontais.
ATTENDANCE_VERTICAL_WIDTH_PT = 1.45


def normalize_attendance_vertical_dividers(doc, layout: dict,
                                            roster_pages: list[dict],
                                            required_rows: int) -> None:
    """Traça uma unica vertical continua por coluna e por pagina usada.

    O arquivo institucional contem divisorias com intensidade irregular.
    Um traco preto uniforme sobre cada eixo fixo do layout corrige essa
    diferenca sem apagar a arte do modelo nem interferir nas horizontais.
    """
    columns = layout['columns']
    boundaries = (
        float(columns['number'][0]),
        float(columns['name'][0]),
        float(columns['area'][0]),
        float(columns['unit'][0]),
        float(columns['signature'][0]),
        float(columns['signature'][1]),
    )
    remaining = required_rows
    for info in roster_pages:
        if remaining <= 0:
            break
        row_count = min(remaining, len(info['rows']))
        if row_count <= 0:
            break
        first_row = info['rows'][0]
        last_row = info['rows'][row_count - 1]
        y_top = float(first_row['top'])
        y_bottom = float(last_row['bottom'])
        if y_bottom <= y_top:
            raise ValueError('Limites invalidos para a grade de presenca.')
        pg = doc[info['page']]
        for x in boundaries:
            pg.draw_line((x, y_top), (x, y_bottom),
                         color=(0, 0, 0),
                         width=ATTENDANCE_VERTICAL_WIDTH_PT, overlay=True)
        remaining -= row_count
    if remaining:
        raise ValueError('A grade nao contem as linhas de presenca necessarias.')


def build_pdf(payload: dict, employees: list[dict]) -> tuple[str, dict]:
    if not _active_template_path().exists() or not LAYOUT_PATH.exists():
        raise RuntimeError('O modelo institucional não foi encontrado. Reinstale o pacote da Plataforma DDS.')
    topic=str(payload.get('topic','')).strip()
    shift=normalize_shift(payload.get('shift',''))
    raw_site=str(payload.get('work_site','')).strip()
    if not canonical_site(raw_site):
        raw_site=str(payload.get('unit', UNIT_CODE)).strip()
    work_site=canonical_site(raw_site)
    # The institutional output always has a one-hour duration, regardless of stale UI payloads.
    duration='01:00'
    iso_date=str(payload.get('date','')).strip()
    content=str(payload.get('content','')).strip()
    if not topic: raise ValueError('Informe o tema do DDS.')
    if len(topic)>140: raise ValueError('O tema deve ter até 140 caracteres para respeitar o campo do formulário.')
    if shift not in SHIFTS: raise ValueError('Selecione um turno válido.')
    if work_site not in WORK_SITES: raise ValueError('Selecione uma unidade válida: GIG, TECA, UNITED ou GATE GOURMET.')
    if len(content)>2600: raise ValueError('O conteúdo ultrapassa o limite de 2.600 caracteres. Resuma o texto antes de gerar o PDF.')
    if iso_date:
        try: display_date=datetime.strptime(iso_date,'%Y-%m-%d').strftime('%d/%m/%Y')
        except Exception: raise ValueError('Informe uma data válida para o DDS.')
    else:
        display_date=''
    if not content:
        content=f'POR QUE É IMPORTANTE?\nO tema “{topic}” merece atenção porque atitudes preventivas reduzem riscos, protegem as pessoas e ajudam a manter a qualidade do serviço.\n\nOBJETIVO\nReforçar práticas seguras, reconhecer situações de risco e orientar a equipe sobre como agir corretamente no trabalho.\n\nORIENTAÇÕES PRÁTICAS\nSiga os procedimentos da empresa, utilize os recursos de proteção indicados, mantenha a área organizada e comunique imediatamente condições inseguras à liderança.\n\nMENSAGEM FINAL\nSegurança é responsabilidade de todos. Pare, avalie o risco e escolha sempre a atitude mais segura.'
    # Responsible persons are determined by the selected work site. Only GIG varies by shift.
    if work_site == 'GIG':
        supervisor_names = SUPERVISORS[shift]
    else:
        supervisor_names = [UNIT_SUPERVISORS[work_site]]
    same_shift=[e for e in employees if e.get('shift')==shift]
    def employee_site(employee):
        return (canonical_site(employee.get('work_site'))
                or canonical_site(employee.get('operational_unit'))
                or canonical_site(employee.get('sector')))
    chosen=[e for e in same_shift if normalize(employee_site(e))==normalize(work_site)]
    requested_ids=payload.get('employee_ids')
    if isinstance(requested_ids,list) and requested_ids:
        requested_set={str(item) for item in requested_ids}
        chosen=[e for e in chosen if str(e.get('id','')) in requested_set]
    manual_blank = payload.get('manual_blank') is True
    if not chosen and not manual_blank:
        raise ValueError('Nenhum colaborador corresponde ao turno e à unidade selecionados. O PDF não foi emitido para evitar uma lista incorreta. Confira os filtros e os cadastros ou selecione “DDS em branco — preenchimento manual”.')
    layout=json.loads(LAYOUT_PATH.read_text(encoding='utf-8'))
    template_capacity=sum(len(p['rows']) for p in layout['pages'])
    required_rows=template_capacity if manual_blank else len(chosen)+BLANK_ATTENDANCE_ROWS
    doc=fitz.open(_active_template_path())
    # Temporariamente desativada a normalização global das linhas: a rotina anterior
    # causava falha no PyMuPDF e timeout durante a geração do PDF.
    roster_pages=prepare_attendance_pages(doc, layout, required_rows)
    # Queue text per page: insert_text wraps existing streams on every call.
    # Batching avoids repeated content-stream processing when many attendance rows are printed.
    writers = {index: fitz.TextWriter(doc[index].rect) for index in range(len(doc))}
    p=doc[0]
    title_size=10.7
    title_left, title_right = 101.0, 568.0
    while title_size>7.0 and fitz.get_text_length(topic.upper(),fontname='tibo',fontsize=title_size)>title_right-title_left-4.0:
        title_size-=0.3
    centered_text(p,topic.upper(),title_left,title_right,105.1,font='tibo',size=title_size,min_size=7.0,writer=writers[0])
    shrunk=draw_content(p,content,writer=writers[0])

    # Fixed fields in the same cells as the supplied institutional document.
    centered_text(p,work_site,504.0,568.0,318.1,font='helv',size=8.0,min_size=6.0,writer=writers[0])
    turn_display={'DIURNO':'MANHÃ / DIURNO','VESPERTINO':'TARDE / VESPERTINO','NOTURNO':'MADRUGADA / NOTURNO'}[shift]
    centered_text(p,turn_display,100.5,206.5,341.8,font='helv',size=7.8,min_size=6.0,writer=writers[0])
    centered_text(p,duration,402.8,448.6,341.8,font='helv',size=8.0,min_size=6.0,writer=writers[0])
    centered_text(p,display_date,504.0,568.0,341.8,font='helv',size=8.0,min_size=6.0,writer=writers[0])
    # Remove any original supervisor text from the template before printing the selected shift.
    # The template may contain full names or split text spans; erase only matching spans in this cell.
    supervisor_left, supervisor_right = 53.0, 269.0
    supervisor_top, supervisor_bottom = 358.5, 395.5
    known_supervisor_tokens = set()
    for name_list in SUPERVISORS.values():
        for known_name in name_list:
            known_supervisor_tokens.update(re.findall(r'[A-ZÀ-Ü0-9]+', known_name.upper()))
    for known_name in UNIT_SUPERVISORS.values():
        known_supervisor_tokens.update(re.findall(r'[A-ZÀ-Ü0-9]+', known_name.upper()))

    redactions_added = 0
    try:
        for block in p.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    span_text = re.sub(r'[^A-Z0-9]', '', str(span.get("text", "")).upper())
                    if not span_text or not any(token in span_text for token in known_supervisor_tokens):
                        continue
                    x0, y0, x1, y1 = map(float, span.get("bbox", (0, 0, 0, 0)))
                    # Only touch the responsible-person field; never mask body text or another cell.
                    if x0 < 45.0 or x1 > 280.0 or y0 < 345.0 or y1 > 410.0:
                        continue
                    rect = fitz.Rect(max(48.8, x0 - 0.8), max(345.0, y0 - 0.8),
                                     min(276.5, x1 + 0.8), min(410.0, y1 + 0.8))
                    if rect.width > 0 and rect.height > 0:
                        p.add_redact_annot(rect, fill=False, cross_out=False)
                        redactions_added += 1
        # Algumas versões do modelo trazem "1" e "2" pré-impressos
        # nos quadradinhos. Eliminar somente esses algarismos, nunca as linhas.
        for original_word in p.get_text("words"):
            x0, y0, x1, y1, word = original_word[:5]
            if (word.strip() in ('1', '2', '01', '02', '01.', '02.')
                    and 25.0 < x0 < 46.0 and x1 < 47.2
                    and 364.0 < y0 < 396.0 and y1 < 398.0):
                p.add_redact_annot(fitz.Rect(x0 - 0.3, y0 - 0.3,
                                             x1 + 0.3, y1 + 0.3),
                                   fill=False, cross_out=False)
                redactions_added += 1
        if redactions_added:
            # Excluir caracteres preimpressos sem fundo e sem tocar os vetores.
            p.apply_redactions(images=2, graphics=0, text=0)
    except Exception:
        # Never paint over the institutional cell as a fallback.
        pass

    # No background overlay: retain the original cell and all institutional rules.
    # Existing printed supervisor glyphs are removed via targeted PDF text
    # redactions above; only the selected names are drawn in this cell.
    # Padrao institucional aprovado aplicado a TODOS os turnos e unidades.
    # Mantemos sempre dois espacos de linha, mesmo se houver apenas um supervisor.
    # A primeira linha conserva exatamente a altura aprovada para Orlando;
    # a segunda conserva a altura aprovada para Evelyn.
    if len(supervisor_names) > len(RESPONSIBLE_ROW_ADJUST_Y):
        raise ValueError('O modelo institucional aceita ate dois responsaveis por DDS.')
    supervisor_row_height = (supervisor_bottom - supervisor_top) / 2.0
    for supervisor_index, supervisor_name in enumerate(supervisor_names, start=1):
        row_top = supervisor_top + (supervisor_index - 1) * supervisor_row_height
        row_bottom = row_top + supervisor_row_height
        row_baseline = (
            (row_top + row_bottom) / 2 + 4.0
            + RESPONSIBLE_ROW_ADJUST_Y[supervisor_index - 1]
        )
        # Numero (01/02) centralizado no quadradinho original à esquerda.
        number_cell_left, number_cell_right = RESPONSIBLE_NUMBER_CELL
        number_text = f'{supervisor_index:02d}'
        number_width = fitz.get_text_length(number_text, fontname='helv', fontsize=7.0)
        number_x = number_cell_left + (number_cell_right - number_cell_left - number_width) / 2.0
        writers[0].append((number_x, row_baseline), number_text,
                          font=fitz.Font(fontname='helv'), fontsize=7.0)
        # Apenas o nome na celula larga, com entreletras padronizado e sem fundo.
        tracked_centered_text(writers[0], supervisor_name,
                              supervisor_left + RESPONSIBLE_NAME_LEFT_INSET,
                              supervisor_right, row_baseline,
                              size=7.0, letter_spacing=RESPONSIBLE_LETTER_SPACING)

    # Populate the exact number of selected-shift rows plus five extraordinary blank slots.
    columns=layout['columns']
    row_number=0
    for page_info in roster_pages:
        pg=doc[page_info['page']]
        for row in page_info['rows']:
            if row_number>=required_rows:
                break
            row_number+=1
            cy=float(row['center'])
            baseline=cy+2.9
            page_writer = writers[page_info['page']]
            centered_text(pg,str(row_number),float(columns['number'][0]),float(columns['number'][1]),baseline,
                          font='helv',size=7.8,min_size=6.0,writer=page_writer)
            if not manual_blank and row_number<=len(chosen):
                employee=chosen[row_number-1]
                name=str(employee.get('name','')).strip()
                name_size=8.2
                name_x=float(columns['name'][0])+1.0 + 4.0 * fitz.get_text_length(' ', fontname='helv', fontsize=name_size)
                name_right=float(columns['name'][1])-2.0
                while name_size>=5.8 and fitz.get_text_length(name,fontname='helv',fontsize=name_size)>name_right-name_x:
                    name_size-=0.25
                if fitz.get_text_length(name,fontname='helv',fontsize=name_size)>name_right-name_x:
                    raise ValueError(f'O nome “{name}” é longo demais para caber na linha do modelo institucional. Ajuste o cadastro ou utilize um nome abreviado aprovado pela liderança.')
                page_writer.append((name_x,baseline), name, font=fitz.Font(fontname='helv'), fontsize=name_size)
            # Area and unit are prefilled on employee and extraordinary rows, exactly as requested.
            centered_text(pg,'LIMPEZA',float(columns['area'][0]),float(columns['area'][1]),baseline,font='helv',size=7.8,min_size=5.8,writer=page_writer)
            centered_text(pg,work_site,float(columns['unit'][0]),float(columns['unit'][1]),baseline,font='helv',size=7.8,min_size=5.8,writer=page_writer)

    finalize_pdf_pagination_and_footer(doc,work_site,writers=writers)
    for page_index, writer in writers.items():
        writer.write_text(doc[page_index], color=(0,0,0), overlay=True)
    # Ultima camada vetorial: normaliza todas as divisorias, uma vez por pagina,
    # em vez de engrossar trechos curtos em cada linha da tabela.
    normalize_attendance_vertical_dividers(doc, layout, roster_pages, required_rows)
    emission_id=uuid.uuid4().hex[:8]
    generated_at=datetime.now()
    date_label=display_date.replace('/','-') if display_date else 'SEM_DATA'
    fname=f'DDS_{date_label}_{slugify(topic)}_{shift.lower()}_{generated_at.strftime("%H%M%S")}_{emission_id}.pdf'
    target=_output_dir()/fname
    temp_target=target.with_name(target.stem + '.tmp.pdf')
    try:
        doc.save(temp_target,garbage=4,deflate=True,clean=True)
        doc.close()
        if not temp_target.exists() or temp_target.stat().st_size < 1000:
            raise RuntimeError('O arquivo PDF gerado está incompleto. Tente novamente e consulte dds_generation.log.')
        with fitz.open(temp_target) as check_doc:
            if len(check_doc) < 1 or check_doc.is_encrypted:
                raise RuntimeError('O PDF foi criado, mas não passou na conferência de integridade.')
        os.replace(str(temp_target), str(target))
    except Exception:
        try: doc.close()
        except Exception: pass
        try:
            if temp_target.exists(): temp_target.unlink()
        except OSError: pass
        raise
    meta={'id':uuid.uuid4().hex[:12],'topic':topic,'shift':shift,'sector':'LIMPEZA','unit':work_site,'work_site':work_site,'date':display_date,
          'duration':duration,'supervisors':supervisor_names,'participants':0 if manual_blank else len(chosen),'manual_blank':manual_blank,'file':fname,
          'created_at':generated_at.isoformat(timespec='seconds'),'warnings':[],'font_adjusted':shrunk,
          'template_pages':_count_pdf_pages(target),'template_capacity':template_capacity,'attendance_rows':required_rows,
          'blank_rows':BLANK_ATTENDANCE_ROWS}
    return fname,meta


def template_capacity() -> int:
    """Return the count of preprinted roster rows in the supplied official template."""
    try:
        layout=json.loads(LAYOUT_PATH.read_text(encoding='utf-8'))
        return sum(len(page.get('rows',[])) for page in layout.get('pages',[]))
    except (OSError, ValueError, TypeError):
        return 107

@app.get('/')
def index():
    return send_from_directory(WEB_DIR,'index.html')

@app.get('/api/health')
def health():
    return jsonify({'status':'ok','app':'Gerador de DDS','version':'1.3.0-web-ready' if HOSTED_MODE else '1.3.0-local','central_auth_required':CENTRAL_AUTH_ENABLED,'persistent_storage_configured':persistent_storage_available()})


# Cofre durável exclusivo do Gerador DDS: Supabase separado do banco familiar.
# A identidade vem da sessão do proprietário ou do JWT já validado pela Central.
# A chave de máquina do proprietário reside SOMENTE no ambiente do Render.
def _vault_proxy(action: str, method: str = 'GET',
                 body: bytes | None = None, filename: str = '') -> Response:
    endpoint = os.environ.get('DDS_VAULT_ENDPOINT', '').strip().rstrip('/')
    if not HOSTED_MODE or not endpoint.startswith('https://'):
        return jsonify({'error': 'Armazenamento durável ainda não está disponível.'}), 503
    owner_id = str(session.get('dds_owner_user_id') or '')
    owner_logged = _owner_access_configured() and hmac.compare_digest(owner_id, OWNER_ACCESS_USER_ID)
    headers = {'Accept': 'application/pdf' if action == 'pdf' and method == 'GET' else 'application/json',
               'apikey': 'sb_publishable_h1TBieh4mxQj0DZXDNu31w_C9HDenLR'}
    if owner_logged:
        owner_token = os.environ.get('DDS_VAULT_OWNER_TOKEN', '').strip()
        if len(owner_token) < 50:
            return jsonify({'error': 'Cofre do proprietário não configurado.'}), 503
        headers['X-DDS-Owner-Token'] = owner_token
    else:
        bearer = request.headers.get('Authorization', '')
        if not bearer.lower().startswith('bearer '):
            return jsonify({'error': 'Entre novamente na Central para acessar o cofre.'}), 401
        headers['Authorization'] = bearer
    if action not in ('state', 'pdf'):
        return jsonify({'error': 'Operação não autorizada.'}), 400
    url = endpoint + '?action=' + action
    if action == 'pdf':
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,180}\.pdf', filename, re.I):
            return jsonify({'error': 'Nome do PDF inválido.'}), 400
        url += '&file=' + quote_plus(filename)
    if body is not None:
        headers['Content-Type'] = 'application/pdf' if action == 'pdf' else 'application/json'
    outgoing = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(outgoing, timeout=18) as upstream:
            payload = upstream.read(12 * 1024 * 1024)
            status = upstream.status
            returned_type = upstream.headers.get('Content-Type', 'application/json')
    except HTTPError as exc:
        status = exc.code
        payload = exc.read(4096)
        returned_type = 'application/json'
    except (URLError, TimeoutError, OSError) as exc:
        app.logger.warning('DDS durable vault request failed: %s', type(exc).__name__)
        return jsonify({'error': 'Armazenamento durável temporariamente indisponível.'}), 503
    if action == 'pdf' and method == 'GET' and status == 200:
        return Response(payload, status=200, mimetype='application/pdf',
                        headers={'Cache-Control': 'private, no-store',
                                 'Content-Disposition': 'inline; filename="' + filename + '"'})
    return Response(payload, status=status, content_type=returned_type,
                    headers={'Cache-Control': 'no-store'})


@app.get('/api/vault/status')
def dds_vault_status():
    configured = bool(HOSTED_MODE and
        os.environ.get('DDS_VAULT_ENDPOINT','').startswith('https://'))
    return jsonify({'configured': configured})


@app.get('/api/vault/state')
def dds_vault_get_state():
    return _vault_proxy('state')


@app.put('/api/vault/state')
def dds_vault_save_state():
    if request.content_length is not None and request.content_length > 2 * 1024 * 1024:
        return jsonify({'error': 'Backup ultrapassa 2 MB.'}), 413
    body = request.get_data(cache=False)
    if len(body) > 2 * 1024 * 1024:
        return jsonify({'error': 'Backup ultrapassa 2 MB.'}), 413
    return _vault_proxy('state', 'PUT', body)


@app.get('/api/vault/pdf/<filename>')
def dds_vault_get_pdf(filename):
    return _vault_proxy('pdf', 'GET', filename=filename)


@app.post('/api/vault/pdf/<filename>')
def dds_vault_save_pdf(filename):
    if request.content_length is not None and request.content_length > 10 * 1024 * 1024:
        return jsonify({'error': 'PDF ultrapassa 10 MB.'}), 413
    body = request.get_data(cache=False)
    if not 20 <= len(body) <= 10 * 1024 * 1024 or not body.startswith(b'%PDF-'):
        return jsonify({'error': 'PDF inválido ou muito grande.'}), 400
    return _vault_proxy('pdf', 'POST', body, filename=filename)



# Em hospedagem, a gravação durável também acontece no servidor.
# Não depende de manter a aba do navegador aberta após confirmar um DDS.
@app.after_request
def persist_dds_mutations_in_vault(response):
    if (not HOSTED_MODE or not os.environ.get('DDS_VAULT_ENDPOINT')
            or request.method not in {'POST','PUT','PATCH','DELETE'}
            or not 200 <= response.status_code < 300):
        return response
    path=request.path
    tracked=(
        path.startswith('/api/topics') or path.startswith('/api/employees')
        or path in {'/api/import-roster','/api/restore-backup',
                    '/api/generate','/api/generate-bulk',
                    '/api/template','/api/template/reset'}
    )
    if not tracked:
        return response
    try:
        snapshot=backup_payload()
        body=json.dumps(snapshot,ensure_ascii=False,separators=(',',':')).encode('utf-8')
        if len(body) <= 2 * 1024 * 1024:
            saved=_vault_proxy('state','PUT',body)
            response.headers['X-DDS-Vault-State'] = 'ok' if saved.status_code==200 else 'unavailable'
            if saved.status_code!=200:
                app.logger.warning('DDS: backup remoto falhou apos uma alteracao local, HTTP %s',saved.status_code)
        else:
            response.headers['X-DDS-Vault-State']='too-large'
    except Exception as exc:
        response.headers['X-DDS-Vault-State']='unavailable'
        app.logger.warning('DDS: falha ao preparar backup remoto: %s',type(exc).__name__)
    if path in {'/api/generate','/api/generate-bulk'}:
        try:
            result=response.get_json(silent=True) or {}
            if path=='/api/generate':
                files=[result.get('metadata',{}).get('file')]
            else:
                files=[item.get('metadata',{}).get('file')
                       for item in result.get('generated',[])]
            file_uploaded=True
            for filename in files:
                if not filename or not re.fullmatch(r'[A-Za-z0-9_.-]{1,180}\.pdf',filename,re.I):
                    file_uploaded=False
                    continue
                candidate=_output_dir()/filename
                if not candidate.is_file() or candidate.stat().st_size>10*1024*1024:
                    file_uploaded=False
                    continue
                saved=_vault_proxy('pdf','POST',candidate.read_bytes(),filename)
                if saved.status_code!=200:
                    file_uploaded=False
                    app.logger.warning('DDS: PDF sem backup duravel: %s, HTTP %s',
                                       filename, saved.status_code)
            response.headers['X-DDS-Vault-Pdf']='ok' if file_uploaded and files else 'unavailable'
        except Exception as exc:
            app.logger.warning('DDS: nao foi possivel sincronizar PDF: %s',type(exc).__name__)
            response.headers['X-DDS-Vault-Pdf']='unavailable'
    return response


@app.get('/api/template')
def get_template_settings():
    settings = {}
    try:
        settings = json.loads(_template_settings_path().read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        pass
    custom_path = _tenant_data_dir() / 'templates' / str(settings.get('custom_filename', ''))
    active = settings.get('active') if settings.get('active') == 'custom' and custom_path.is_file() else 'default'
    return jsonify({'ok': True, 'active': active, 'name': settings.get('display_name', '') if active == 'custom' else 'Modelo institucional disponível', 'upload_limit_mb': 15})

@app.post('/api/template')
def upload_template():
    uploaded = request.files.get('file')
    if not uploaded or not uploaded.filename:
        return jsonify({'error': 'Selecione um arquivo PDF institucional.'}), 400
    if Path(uploaded.filename).suffix.lower() != '.pdf':
        return jsonify({'error': 'Formato não suportado. Envie um modelo institucional em PDF.'}), 400
    template_dir = _tenant_data_dir() / 'templates'
    template_dir.mkdir(parents=True, exist_ok=True)
    candidate = template_dir / ('template_custom_' + uuid.uuid4().hex[:12] + '.pdf')
    try:
        uploaded.save(candidate)
        if candidate.stat().st_size <= 0 or candidate.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError('O arquivo está vazio ou ultrapassa o limite de 15 MB.')
        with fitz.open(candidate) as document:
            if len(document) < 3 or len(document) > 100:
                raise ValueError('O PDF deve conter de 3 a 100 páginas, incluindo a página de continuação da lista de presença.')
        settings = {'active': 'custom', 'custom_filename': candidate.name,
                    'display_name': secure_filename(uploaded.filename)[:120] or 'Modelo institucional personalizado',
                    'updated_at': datetime.now().isoformat(timespec='seconds')}
        _template_settings_path().write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
    except (ValueError, RuntimeError, OSError) as exc:
        try: candidate.unlink(missing_ok=True)
        except OSError: pass
        return jsonify({'error': 'Não foi possível validar o PDF: ' + str(exc)[:220]}), 400
    return jsonify({'ok': True, 'active': 'custom', 'name': settings['display_name'],
                    'message': 'Modelo PDF validado e ativado. Confira um DDS de teste antes de usar operacionalmente.'})

@app.post('/api/template/reset')
def reset_template():
    try:
        settings = json.loads(_template_settings_path().read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        settings = {}
    settings['active'] = 'default'
    settings['updated_at'] = datetime.now().isoformat(timespec='seconds')
    _template_settings_path().write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
    return jsonify({'ok': True, 'active': 'default', 'name': 'Modelo institucional disponível',
                    'message': 'O modelo institucional original foi restaurado.'})


@app.get('/api/state')
def state_endpoint():
    state=read_state()
    return jsonify({'employees':state['employees'],'history':state['history'],'settings':state['settings'],
                    'unit_code':UNIT_CODE,'work_sites':WORK_SITES,'units':WORK_SITES,
                    'shifts':SHIFTS,'supervisors':SUPERVISORS,'unit_supervisors':UNIT_SUPERVISORS,'capacity':template_capacity(),'blank_attendance_rows':BLANK_ATTENDANCE_ROWS,'capacity_mode':'dynamic',
                    **storage_status()})

def backup_payload() -> dict:
    state = read_state()
    all_topics = topic_rows()
    backup_fields = ('title','category','objective','content','source','created_at','updated_at')
    # Armazena metadados de origem sem duplicar os campos adicionais da API.
    custom_topics = [{key: row.get(key) for key in backup_fields}
                     for row in all_topics if row.get('source') == 'cadastrado pelo usuário']
    researched_topics = [{key: row.get(key) for key in backup_fields}
                         for row in all_topics
                         if str(row.get('source','')).startswith('WEB-PESQUISADO:')]
    return {
        'format': 'gerador-dds-backup',
        'backup_version': 1,
        'app_version': '1.3.0',
        'exported_at': datetime.now().isoformat(timespec='seconds'),
        'state': state,
        'custom_topics': custom_topics,
        'researched_topics': researched_topics,
        'scope': 'Cadastros, configurações, histórico, temas personalizados e temas pesquisados online com referências. Os PDFs já gerados permanecem no armazenamento próprio e não integram este JSON.'
    }


@app.get('/api/backup-data')
def export_backup_data():
    return jsonify(backup_payload())

@app.get('/api/backup-diagnostics')
def dds_backup_diagnostics():
    """Read-only, authenticated verification. Never replace the cloud snapshot."""
    upstream = _vault_proxy('state')
    if isinstance(upstream, tuple):
        return upstream
    if upstream.status_code != 200:
        return jsonify({'ok': False, 'vault_http_status': upstream.status_code,
                        'error': 'Não foi possível conferir o cofre remoto.'}), 503
    data = upstream.get_json(silent=True)
    backup = data.get('backup') if isinstance(data, dict) else None
    if not isinstance(backup, dict) or backup.get('format') != 'gerador-dds-backup':
        return jsonify({'ok': False, 'vault_accessible': True,
                        'backup_present': False,
                        'error': 'Nenhum backup DDS válido foi encontrado no cofre.'}), 503
    saved_state = backup.get('state')
    if not isinstance(saved_state, dict) or not isinstance(saved_state.get('employees'), list):
        return jsonify({'ok': False, 'vault_accessible': True,
                        'backup_present': True,
                        'error': 'A estrutura do backup remoto é inválida.'}), 503
    remote_people = len(saved_state['employees'])
    remote_history = len(saved_state.get('history', [])) if isinstance(saved_state.get('history'), list) else None
    local_state = read_state()
    local_people = len(local_state.get('employees', []))
    return jsonify({
        'ok': True, 'vault_accessible': True, 'backup_present': True,
        'employee_count_remote': remote_people,
        'employee_count_local': local_people,
        'history_count_remote': remote_history,
        'research_topics_in_backup': isinstance(backup.get('researched_topics'), list),
        'local_has_fewer_employees': local_people < remote_people,
        'snapshot_exported_at': backup.get('exported_at'),
        'note': 'Conferência somente leitura; nenhum backup foi restaurado ou substituído.'
    })




@app.post('/api/restore-backup')
def restore_backup_data():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({'error': 'Arquivo de backup inválido.'}), 400
    if body.get('format') != 'gerador-dds-backup' or body.get('backup_version') != 1:
        return jsonify({'error': 'Arquivo de backup inválido ou de versão não compatível.'}), 400
    imported_state = body.get('state')
    custom_topics = body.get('custom_topics', [])
    researched_topics = body.get('researched_topics', [])
    if not isinstance(imported_state, dict) or not isinstance(imported_state.get('employees', []), list):
        return jsonify({'error': 'O backup não contém uma relação válida de colaboradores.'}), 400
    if not isinstance(imported_state.get('history', []), list) or not isinstance(imported_state.get('settings', {}), dict):
        return jsonify({'error': 'O histórico ou as configurações do backup são inválidos.'}), 400
    if not isinstance(custom_topics, list) or len(custom_topics) > 5000:
        return jsonify({'error': 'A lista de temas do backup é inválida ou excede o limite permitido.'}), 400
    if not isinstance(researched_topics, list) or len(researched_topics) > 5000:
        return jsonify({'error': 'A lista de temas pesquisados é inválida ou excede o limite permitido.'}), 400
    raw_employees = imported_state.get('employees', [])
    raw_history = imported_state.get('history', [])
    if len(raw_employees) > 20000 or len(raw_history) > 10000:
        return jsonify({'error': 'O backup ultrapassa os limites de cadastros ou histórico permitidos.'}), 400
    employees = []
    seen_ids = set()
    for raw in raw_employees:
        if not isinstance(raw, dict) or not str(raw.get('name', '')).strip():
            return jsonify({'error': 'Há um colaborador inválido no backup. Nenhum dado foi restaurado.'}), 400
        raw_shift = str(raw.get('shift', '')).strip()
        normalized_shift = normalize_shift(raw_shift)
        if raw_shift and not normalized_shift:
            return jsonify({'error': f'Turno inválido no cadastro de {str(raw.get("name", "")).strip()}.'}), 400
        cleaned = clean_employee({**raw, 'shift': normalized_shift})
        if cleaned['id'] in seen_ids:
            return jsonify({'error': 'O backup contém identificadores repetidos de colaboradores.'}), 400
        seen_ids.add(cleaned['id'])
        employees.append(cleaned)
    if any(not isinstance(row, dict) for row in raw_history):
        return jsonify({'error': 'Há um registro inválido no histórico do backup.'}), 400
    normalized_topics = []
    seen_titles = set()
    for raw in custom_topics:
        if not isinstance(raw, dict):
            return jsonify({'error': 'Há um tema inválido no backup.'}), 400
        title = str(raw.get('title', '')).strip()
        category = str(raw.get('category', '')).strip() or 'Geral'
        objective = str(raw.get('objective', '')).strip()
        content = str(raw.get('content', '')).strip()
        title_key = normalize(title)
        if not title or len(title) > 140 or len(category) > 80 or len(objective) > 300 or not content or len(content) > 2600:
            return jsonify({'error': 'Um tema do backup está incompleto ou excede o limite permitido.'}), 400
        if title_key in seen_titles:
            return jsonify({'error': 'O backup contém temas personalizados repetidos.'}), 400
        seen_titles.add(title_key)
        normalized_topics.append((title, category, objective or f'Reforçar práticas seguras relacionadas a {title.lower()}.', content,
                                  str(raw.get('created_at') or datetime.now().isoformat(timespec='seconds')),
                                  str(raw.get('updated_at') or datetime.now().isoformat(timespec='seconds'))))
    normalized_researched = []
    for raw in researched_topics:
        if not isinstance(raw, dict):
            return jsonify({'error': 'Há um tema pesquisado inválido no backup.'}), 400
        title = str(raw.get('title', '')).strip()
        category = str(raw.get('category', '')).strip() or 'Pesquisa na internet — revisar antes de usar'
        objective = str(raw.get('objective', '')).strip()
        content = str(raw.get('content', '')).strip()
        provenance = str(raw.get('source', ''))
        if not title or len(title)>140 or len(category)>80 or len(objective)>300 or not content or len(content)>2600:
            return jsonify({'error': 'Um tema pesquisado está incompleto ou excede o limite permitido.'}), 400
        if not provenance.startswith('WEB-PESQUISADO:') or len(provenance)>12000:
            return jsonify({'error': 'Referências do tema pesquisado inválidas.'}), 400
        try:
            sources = json.loads(provenance.partition(':')[2])
        except (ValueError, TypeError):
            return jsonify({'error': 'Referências do tema pesquisado ilegíveis.'}), 400
        if (not isinstance(sources, list) or not 1<=len(sources)<=8
                or not all(isinstance(item, dict) and
                           _approved_source_url(str(item.get('url',''))) for item in sources)):
            return jsonify({'error': 'Fontes do tema pesquisado são inválidas.'}), 400
        title_key = normalize(title)
        if title_key in seen_titles:
            return jsonify({'error': 'O backup contém títulos de temas repetidos.'}), 400
        seen_titles.add(title_key)
        normalized_researched.append((title,category,
            objective or f'Reforçar a prevenção dos riscos relacionados a {title.lower()}.',
            content,provenance,
            str(raw.get('created_at') or datetime.now().isoformat(timespec='seconds')),
            str(raw.get('updated_at') or datetime.now().isoformat(timespec='seconds'))))
    # Preserve a recoverable snapshot before replacing any live data.
    backups_dir = _tenant_data_dir() / 'backups'
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:4]
    (backups_dir / f'antes_restauracao_{stamp}.json').write_text(
        json.dumps(backup_payload(), ensure_ascii=False, indent=2), encoding='utf-8')
    init_topics_db()
    try:
        with closing(sqlite3.connect(_topics_db_path())) as con:
            con.execute('BEGIN')
            # A restauração de temas é aditiva: não descartar edições locais recentes.
            for title, category, objective, content, created_at, updated_at in normalized_topics:
                con.execute('''INSERT OR IGNORE INTO topics(title,category,objective,content,source,created_at,updated_at)
                               VALUES(?,?,?,?,?,?,?)''',
                            (title, category, objective, content, 'cadastrado pelo usuário', created_at, updated_at))
            # Restaurar pesquisas de forma aditiva: nunca excluir ou sobrescrever
            # um tema personalizado ou um tema online mais recente.
            for title,category,objective,content,provenance,created_at,updated_at in normalized_researched:
                con.execute('''INSERT OR IGNORE INTO topics(title,category,objective,content,source,created_at,updated_at)
                               VALUES(?,?,?,?,?,?,?)''',
                            (title,category,objective,content,provenance,created_at,updated_at))
            con.commit()
    except Exception:
        return jsonify({'error': 'Não foi possível restaurar os temas. O estado atual foi preservado em uma cópia de segurança local.'}), 500
    restored_state = {
        'employees': employees,
        'history': raw_history,
        'settings': imported_state.get('settings', default_state()['settings'])
    }
    write_state(restored_state)
    return jsonify({'ok': True, 'employees': len(employees), 'history': len(restored_state['history']),
                    'custom_topics': len(normalized_topics), 'researched_topics': len(normalized_researched),
                    'message': 'Backup restaurado. Foi salva uma cópia dos dados anteriores neste computador.'})

@app.post('/api/research-topic')
def research_topic():
    body=request.get_json(silent=True) or {}
    title=str(body.get('title','')).strip()
    if not title: return jsonify({'error':'Informe o tema que deseja pesquisar.'}),400
    if len(title)>140: return jsonify({'error':'O tema deve ter até 140 caracteres.'}),400
    init_topics_db()
    # Reutilizar primeiro o tema exato quando for material da biblioteca inicial.
    # Assim a consulta evita custo e esperas, sem afirmar que houve busca ao vivo.
    saved=_saved_topic_for_reuse(title)
    if saved and saved.get('source') == 'biblioteca inicial':
        return jsonify({
            'ok':True,'mode':'library','title':saved['title'],
            'category':saved['category'],'objective':saved['objective'],
            'content':saved['content'],'sources':[],
            'message':'Tema encontrado na biblioteca inicial. Material de apoio, não uma pesquisa ao vivo; revise antes de apresentar.'
        })
    # A pesquisa desta ação precisa de evidência obtida agora; a biblioteca local
    # não é usada como substituta silenciosa quando as fontes online falham.
    try:
        sources=_web_search(title)
        content=_draft_from_sources(title,sources)
        if content and sources:
            validate_content_fits_pdf(content)
            category='Pesquisa na internet — revisar antes de usar'
            objective=f'Reforçar práticas seguras relacionadas a {title.lower()}, com base nas fontes públicas listadas.'
            now=datetime.now().isoformat(timespec='seconds')
            source_value='WEB-PESQUISADO:'+json.dumps(sources,ensure_ascii=False)
            with closing(sqlite3.connect(_topics_db_path())) as con:
                existing=con.execute('SELECT id,source FROM topics WHERE title=? COLLATE NOCASE',(title,)).fetchone()
                if existing and str(existing[1]).startswith('WEB-PESQUISADO:'):
                    con.execute('UPDATE topics SET category=?,objective=?,content=?,source=?,updated_at=? WHERE id=?',(category,objective,content,source_value,now,existing[0]))
                elif existing:
                    # Keep manually edited and original library records intact.
                    pass
                else:
                    con.execute('INSERT INTO topics(title,category,objective,content,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(title,category,objective,content,source_value,now,now))
                con.commit()
            return jsonify({'ok':True,'mode':'online','title':title,'category':category,'objective':objective,'content':content,'sources':sources,'message':'Rascunho de pesquisa preparado a partir de fontes online. Confira as fontes e revise o conteúdo antes de usar; fontes complementares não são oficiais.'})
    except Exception as exc:
        app.logger.warning('Pesquisa online de DDS indisponível: %s',exc)
    # Biblioteca verificável: sem fingir que os registros são pesquisa ao vivo.
    cached=_saved_topic_for_reuse(title)
    if cached:
        metadata=topic_source_metadata(cached.get('source',''))
        return jsonify({
            'ok':True,'mode':'library',
            'title':cached['title'],'category':cached['category'],
            'objective':cached['objective'],'content':cached['content'],
            'sources':metadata.get('sources',[]),
            'message':'Pesquisa online indisponível. Tema recuperado da biblioteca; valide a atualidade das referências antes do uso.'
        })
    # A consulta solicitada precisa de fontes reais desta execução. Não usar um
    # rascunho genérico, links de busca ou cache como se fossem pesquisa online.
    return jsonify({
        'ok': False,
        'mode': 'research-unavailable',
        'error': 'Não foi possível consultar fontes online verificáveis nesta tentativa. Nenhum tema foi alterado. Tente novamente mais tarde ou edite manualmente um tema existente.'
    }), 503


@app.get('/api/topics')
def list_topics():
    rows=topic_rows(request.args.get('q',''),request.args.get('category',''))
    categories=sorted({row['category'] for row in topic_rows()},key=str.casefold)
    storage = storage_status()
    return jsonify({'topics':rows,'categories':categories,'count':len(rows),
                    'offline':not HOSTED_MODE,**storage})

@app.post('/api/topics')
def create_topic():
    body=request.get_json(silent=True) or {}
    title=str(body.get('title','')).strip()
    category=str(body.get('category','')).strip() or 'Geral'
    objective=str(body.get('objective','')).strip()
    content=str(body.get('content','')).strip()
    if not title: return jsonify({'error':'Informe o título do tema.'}),400
    if len(title)>140: return jsonify({'error':'O título deve ter até 140 caracteres.'}),400
    if not content: return jsonify({'error':'Informe o conteúdo do tema antes de salvar.'}),400
    if len(content)>2600: return jsonify({'error':'O conteúdo deve ter até 2.600 caracteres para caber no formulário.'}),400
    if len(category)>80: return jsonify({'error':'A categoria deve ter até 80 caracteres.'}),400
    if not objective: objective=f'Reforçar a prevenção e as práticas seguras relacionadas a {title.lower()}.'
    try:
        validate_content_fits_pdf(content)
    except ValueError as exc:
        return jsonify({'error':str(exc)}),400
    now=datetime.now().isoformat(timespec='seconds')
    init_topics_db()
    try:
        with closing(sqlite3.connect(_topics_db_path())) as con:
            cur=con.execute('INSERT INTO topics(title,category,objective,content,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                (title,category,objective,content,'cadastrado pelo usuário',now,now))
            topic_id=cur.lastrowid
            con.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error':'Esse tema já existe na biblioteca. Pesquise pelo nome ou use outro título.'}),409
    return jsonify({'ok':True,'topic':next((x for x in topic_rows() if x['id']==topic_id),None)})

@app.route('/api/topics/<int:topic_id>', methods=['PUT'])
def edit_topic(topic_id):
    # Update a saved theme, including a seed theme, while retaining an editable local copy.
    body=request.get_json(silent=True) or {}
    title=str(body.get('title','')).strip()
    category=str(body.get('category','')).strip() or 'Geral'
    objective=str(body.get('objective','')).strip()
    content=str(body.get('content','')).strip()
    if not title: return jsonify({'error':'Informe o título do tema.'}),400
    if len(title)>140: return jsonify({'error':'O título deve ter até 140 caracteres.'}),400
    if len(category)>80: return jsonify({'error':'A categoria deve ter até 80 caracteres.'}),400
    if len(objective)>300: return jsonify({'error':'O objetivo deve ter até 300 caracteres.'}),400
    if not content: return jsonify({'error':'Informe o conteúdo do tema antes de salvar.'}),400
    if len(content)>2600: return jsonify({'error':'O conteúdo deve ter até 2.600 caracteres.'}),400
    try:
        validate_content_fits_pdf(content)
    except ValueError as exc:
        return jsonify({'error':str(exc)}),400
    now=datetime.now().isoformat(timespec='seconds')
    init_topics_db()
    try:
        with closing(sqlite3.connect(_topics_db_path())) as con:
            cur=con.execute('''UPDATE topics SET title=?,category=?,objective=?,content=?,source='cadastrado pelo usuário',updated_at=? WHERE id=?''',
                            (title,category,objective or f'Reforçar a prevenção relacionada a {title.lower()}.',content,now,topic_id))
            if cur.rowcount==0: return jsonify({'error':'Tema não encontrado.'}),404
            con.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error':'Já existe outro tema com esse título. Escolha um título diferente.'}),409
    return jsonify({'ok':True,'topic':next((x for x in topic_rows() if x['id']==topic_id),None)})


@app.delete('/api/topics/<int:topic_id>')
def delete_topic(topic_id):
    init_topics_db()
    with closing(sqlite3.connect(_topics_db_path())) as con:
        cur=con.execute("DELETE FROM topics WHERE id=? AND source='cadastrado pelo usuário'",(topic_id,))
        if cur.rowcount==0:
            return jsonify({'error':'Tema não encontrado ou faz parte da biblioteca inicial protegida.'}),404
        con.commit()
    return jsonify({'ok':True})

@app.post('/api/import-roster')
def import_roster():
    if 'file' not in request.files: return jsonify({'error':'Selecione uma planilha .xlsx para importar.'}),400
    f=request.files['file']
    if not f.filename or not f.filename.lower().endswith(('.xlsx','.xlsm')):
        return jsonify({'error':'Formato não suportado. Salve a planilha como .xlsx e tente novamente.'}),400
    try:
        imported,notes=parse_xlsx(f)
    except Exception as exc:
        return jsonify({'error':f'Não consegui ler a planilha: {str(exc)[:180]}'}),400
    if not imported: return jsonify({'error':'Não encontrei colaboradores. Verifique se há nomes nas abas e se os turnos aparecem no cabeçalho ou no nome de cada aba.','notes':notes}),400
    state=read_state()
    # Preserve a timestamped local backup before replacing the current roster.
    if state.get('employees'):
        backups=_tenant_data_dir()/'backups'; backups.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:4]
        (backups/f'colaboradores_{stamp}.json').write_text(json.dumps(state['employees'],ensure_ascii=False,indent=2),encoding='utf-8')
    # Deliberate replace, not append: each import becomes the current roster snapshot.
    state['employees']=imported
    write_state(state)
    by_shift={s:sum(1 for e in imported if e['shift']==s) for s in SHIFTS}
    unknown=sum(1 for e in imported if not e['shift'])
    sectors=sorted({e['sector'] for e in imported if e['sector']})
    sites=sorted({e['work_site'] for e in imported if e.get('work_site')})
    missing_sector=sum(1 for e in imported if not e.get('sector'))
    missing_site=sum(1 for e in imported if not e.get('work_site'))
    return jsonify({'ok':True,'count':len(imported),'by_shift':by_shift,'unknown_shift':unknown,'sectors':sectors,
                    'work_sites':sites,'units':sites,'missing_sector':missing_sector,'missing_site':missing_site,
                    'missing_unit':missing_site,'unit_code':UNIT_CODE,'notes':notes})

@app.post('/api/employees')
def add_employee():
    body=request.get_json(silent=True) or {}
    name=str(body.get('name','')).strip()
    if not name: return jsonify({'error':'Informe o nome do colaborador.'}),400
    e=clean_employee(body)
    if e['shift'] not in SHIFTS: return jsonify({'error':'Selecione um turno válido.'}),400
    state=read_state()
    key=(normalize(e['name']),e['shift'],normalize(e.get('work_site','')))
    if any((normalize(x['name']),x['shift'],normalize(x.get('work_site') or canonical_site(x.get('operational_unit')) or canonical_site(x.get('sector'))))==key for x in state['employees']):
        return jsonify({'error':'Esse colaborador já consta na lista para o mesmo turno e local de trabalho.'}),409
    state['employees'].append(e); write_state(state)
    return jsonify({'ok':True,'employee':e})

@app.route('/api/employees/<employee_id>', methods=['PUT'])
def edit_employee(employee_id):
    body=request.get_json(silent=True) or {}
    name=str(body.get('name','')).strip()
    if not name: return jsonify({'error':'Informe o nome do colaborador.'}),400
    e=clean_employee({**body,'id':employee_id})
    if e['shift'] not in SHIFTS: return jsonify({'error':'Selecione um turno válido.'}),400
    state=read_state()
    index=next((i for i,x in enumerate(state['employees']) if x.get('id')==employee_id),None)
    if index is None: return jsonify({'error':'Colaborador não encontrado.'}),404
    key=(normalize(e['name']),e['shift'],normalize(e.get('work_site','')))
    if any(i!=index and (normalize(x['name']),x['shift'],normalize(x.get('work_site') or canonical_site(x.get('operational_unit')) or canonical_site(x.get('sector'))))==key for i,x in enumerate(state['employees'])):
        return jsonify({'error':'Já existe um colaborador com esse nome, turno e local de trabalho.'}),409
    # Keep the original source-sheet metadata when editing a roster imported from Excel.
    e['source_sheet']=state['employees'][index].get('source_sheet','')
    state['employees'][index]=e
    write_state(state)
    return jsonify({'ok':True,'employee':e})

@app.delete('/api/employees/<employee_id>')
def remove_employee(employee_id):
    state=read_state(); before=len(state['employees'])
    state['employees']=[e for e in state['employees'] if e.get('id')!=employee_id]
    if len(state['employees'])==before: return jsonify({'error':'Colaborador não encontrado.'}),404
    write_state(state); return jsonify({'ok':True})

@app.post('/api/generate')
def generate():
    body=request.get_json(silent=True) or {}
    state=read_state()
    try:
        fname,meta=build_pdf(body,state['employees'])
    except (ValueError,RuntimeError) as exc:
        return jsonify({'error':str(exc)}),400
    except Exception as exc:
        incident_id=uuid.uuid4().hex[:8]
        try:
            with open(_tenant_data_dir()/'dds_generation.log','a',encoding='utf-8') as log:
                log.write('\n[{0}] {1}\n'.format(datetime.now().isoformat(timespec='seconds'),incident_id))
                log.write('Payload: topic={0!r}, shift={1!r}, site={2!r}, date={3!r}\n'.format(
                    str(body.get('topic',''))[:140],body.get('shift'),body.get('work_site'),body.get('date')))
                log.write(''.join(traceback.format_exception(type(exc),exc,exc.__traceback__)))
        except Exception:
            pass
        app.logger.exception('Falha na geração de PDF (incidente %s)',incident_id)
        return jsonify({'error':'Não foi possível concluir a geração do PDF (código {0}). Confira permissões/espaço em disco e consulte dds_generation.log na pasta de dados local (%LOCALAPPDATA%\\GrupoGPS\\PlataformaDDS, salvo configuração diferente). O arquivo incompleto não foi publicado.'.format(incident_id)}),500
    state['history'].insert(0,meta); state['history']=state['history'][:100]
    write_state(state)
    return jsonify({'ok':True,'metadata':meta,'url':f'/api/files/{fname}'})

@app.post('/api/generate-bulk')
def generate_bulk():
    """Generate one combined institutional PDF for selected topics, shifts and units."""
    body=request.get_json(silent=True) or {}
    state=read_state()
    shifts=body.get('shifts'); units=body.get('units')
    if not isinstance(shifts,list) or not shifts or any(x not in SHIFTS for x in shifts):
        return jsonify({'error':'Marque pelo menos um turno válido.'}),400
    if not isinstance(units,list) or not units or any(canonical_site(x) not in WORK_SITES for x in units):
        return jsonify({'error':'Marque pelo menos uma unidade válida.'}),400
    topics=body.get('topics')
    if not isinstance(topics,list) or not topics:
        topics=[{'topic':str(body.get('topic','')).strip(),'content':str(body.get('content','')).strip()}]
    clean_topics=[]
    for item in topics:
        if not isinstance(item,dict): continue
        title=str(item.get('topic',item.get('title',''))).strip()
        content=str(item.get('content','')).strip()
        if title and content:
            if len(title)>140: return jsonify({'error':f'O tema "{title[:50]}" ultrapassa 140 caracteres.'}),400
            if len(content)>2600: return jsonify({'error':f'O conteúdo do tema "{title[:50]}" ultrapassa 2.600 caracteres.'}),400
            clean_topics.append({'topic':title,'content':content})
    if not clean_topics: return jsonify({'error':'Selecione pelo menos um tema com conteúdo preenchido.'}),400
    iso_date=str(body.get('date','')).strip()
    if iso_date:
        try: datetime.strptime(iso_date,'%Y-%m-%d')
        except Exception: return jsonify({'error':'A data está inválida. Deixe em branco ou escolha uma data válida.'}),400
    units=list(dict.fromkeys(canonical_site(x) for x in units)); shifts=list(dict.fromkeys(shifts))
    generated=[]; skipped=[]; errors=[]; temp_files=[]
    for topic_item in clean_topics:
        for shift in shifts:
            for unit in units:
                matching=[]
                for employee in state['employees']:
                    site=(canonical_site(employee.get('work_site')) or canonical_site(employee.get('operational_unit')) or canonical_site(employee.get('sector')))
                    if employee.get('shift')==shift and normalize(site)==normalize(unit): matching.append(employee)
                blank_mode = body.get('manual_blank') is True
                requested_ids = body.get('employee_ids')
                if isinstance(requested_ids, list) and requested_ids:
                    selected_ids = {str(item) for item in requested_ids}
                    matching = [e for e in matching if str(e.get('id', '')) in selected_ids]
                if not matching and not blank_mode:
                    skipped.append({'topic':topic_item['topic'],'shift':shift,'unit':unit,'reason':'Nenhum colaborador cadastrado para esta combinação.'})
                    continue
                payload={**body,**topic_item,'shift':shift,'work_site':unit,'unit':unit,'sector':'LIMPEZA','duration':'01:00','date':iso_date}
                try:
                    fname,meta=build_pdf(payload,state['employees'])
                    temp_files.append((_output_dir()/fname,meta))
                    generated.append(meta)
                except Exception as exc:
                    errors.append({'topic':topic_item['topic'],'shift':shift,'unit':unit,'error':str(exc)})
    if not temp_files:
        return jsonify({'error':'Nenhum PDF foi gerado. Confira colaboradores, turnos e unidades selecionados.','skipped':skipped,'failures':errors}),400
    # Merge all selected topic/shift/unit documents into one downloadable PDF.
    stamp=datetime.now(); combined_id=uuid.uuid4().hex[:8]
    date_label=iso_date or 'SEM_DATA'
    combined_name=f'DDS_MASSA_{date_label}_{stamp.strftime("%H%M%S")}_{combined_id}.pdf'
    combined_path=_output_dir()/combined_name
    merged=fitz.open()
    try:
        for path,meta in temp_files:
            with fitz.open(path) as part: merged.insert_pdf(part)
        merged.save(combined_path,garbage=4,deflate=True,clean=True)
    finally:
        merged.close()
    with fitz.open(combined_path) as verify:
        if len(verify)<1: raise RuntimeError('O PDF consolidado não passou na conferência de integridade.')
    for path,meta in temp_files:
        try: path.unlink()
        except OSError: pass
    with fitz.open(combined_path) as combined_check:
        combined_pages=len(combined_check)
    combined_meta={'id':combined_id,'topic':f'{len(clean_topics)} tema(s) · {len(generated)} documento(s) reunidos','shift':'MÚLTIPLOS','sector':'LIMPEZA','unit':'MÚLTIPLAS','work_site':'MÚLTIPLAS','date':datetime.strptime(iso_date,'%Y-%m-%d').strftime('%d/%m/%Y') if iso_date else '', 'duration':'01:00','supervisors':[],'participants':sum(x['participants'] for x in generated),'file':combined_name,'created_at':stamp.isoformat(timespec='seconds'),'template_pages':combined_pages,'items':generated}
    state['history'].insert(0,combined_meta); state['history']=state['history'][:100]; write_state(state)
    return jsonify({'ok':True,'generated':[{'metadata':combined_meta,'url':f'/api/files/{combined_name}'}],'items':generated,'skipped':skipped,'failures':errors,'count':1,'document_count':len(generated),'topics_count':len(clean_topics)})


@app.get('/api/files/<path:filename>')
def get_pdf(filename):
    safe=secure_filename(filename)
    path=_output_dir()/safe
    if not path.exists() or path.suffix.lower()!='.pdf': abort(404)
    return send_from_directory(_output_dir(),safe,mimetype='application/pdf',as_attachment=False,download_name=safe)

@app.get('/api/export-roster')
def export_roster():
    state=read_state()
    return jsonify({'exported_at':datetime.now().isoformat(timespec='seconds'),'employees':state['employees'],'settings':state['settings']})

@app.get('/<path:path>')
def catch_static(path):
    candidate=WEB_DIR/path
    if candidate.is_file(): return send_from_directory(WEB_DIR,path)
    abort(404)

if __name__=='__main__':
    port=int(os.environ.get('PORT', os.environ.get('DDS_PORT','8766')))
    if HOSTED_MODE:
        # The hosting provider terminates HTTPS; app-level admin authentication remains mandatory.
        from waitress import serve
        serve(app, host='0.0.0.0', port=port, threads=4)
    else:
        # Local install remains restricted to this computer.
        try:
            from waitress import serve
            serve(app,host='127.0.0.1',port=port,threads=6)
        except ImportError:
            app.run(host='127.0.0.1',port=port,debug=False,threaded=True)
