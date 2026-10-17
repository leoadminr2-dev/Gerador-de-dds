from __future__ import annotations
import json, math, os, re, sqlite3, unicodedata, uuid, traceback, html, time, hmac
from contextlib import closing
from datetime import datetime, date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, quote_plus, urlparse, unquote
from urllib.request import Request, urlopen
from html.parser import HTMLParser
from flask import Flask, jsonify, request, send_from_directory, abort
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
UNIT_SUPERVISORS = {'UNITED': 'Wilker Maria', 'GATE GOURMET': 'Márcia Faustino', 'TECA': 'Pedro Paulo', 'GIG': 'A confirmar'}
SUPERVISORS = {
    'NOTURNO': ['Maria do Socorro'],
    'DIURNO': ['ORLANDO BARBOSA', 'EVELYN MONTOVANI'],
    'VESPERTINO': ['Kayo Lessa', 'Bruna Fraga'],
}
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
if HOSTED_MODE and len(ADMIN_PASSWORD) < 16:
    raise RuntimeError('Hospedagem bloqueada: configure DDS_ADMIN_PASSWORD com pelo menos 16 caracteres antes de iniciar.')


@app.before_request
def require_hosted_admin_auth():
    # Hosted instances require HTTP Basic Auth on the whole application; only health checks are public.
    if not HOSTED_MODE or request.path == '/healthz':
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


@app.get('/healthz')
def healthz():
    return jsonify({'ok': True, 'service': 'Gerador de DDS', 'hosted': HOSTED_MODE})


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
        # Never fetch search result pages or redirector links as source material.
        if host == 'duckduckgo.com' or host.endswith('.duckduckgo.com') or host == 'bing.com' or host.endswith('.bing.com'):
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


def _fetch_text(url: str, timeout: float=5.0) -> str:
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


def _search_engine(query: str, engine: str) -> list[dict]:
    if engine == 'duckduckgo':
        url='https://html.duckduckgo.com/html/?q='+quote_plus(query)
        parser_cls=_SearchResultParser
    else:
        url='https://www.bing.com/search?q='+quote_plus(query)+'&count=10'
        parser_cls=_BingResultParser
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36','Accept-Language':'pt-BR,pt;q=0.9,en;q=0.8'})
    with urlopen(req,timeout=10.0) as response:
        page=response.read(1_500_000).decode('utf-8',errors='replace')
    parser=parser_cls(); parser.feed(page)
    found=[]; seen=set()
    from urllib.parse import parse_qs
    for item in parser.results:
        u=html.unescape(item.get('url','')).strip()
        if u.startswith('//'): u='https:'+u
        parsed=urlparse(u)
        if parsed.netloc.lower().endswith('duckduckgo.com'):
            target=parse_qs(parsed.query).get('uddg',[''])[0]
            if target: u=target
        u=unquote(u)
        if not _approved_source_url(u) or u in seen: continue
        seen.add(u); found.append({'title':item.get('title') or (urlparse(u).hostname or 'Fonte da internet'),'url':u,'snippet':item.get('snippet','')})
        if len(found)>=10: break
    return found


def _web_search(topic: str) -> list[dict]:
    # First search authoritative Brazilian sources; if they do not return usable results,
    # broaden to the public web. Results from non-official sites are explicitly labelled.
    official_query = f'{topic} segurança do trabalho prevenção orientação site:gov.br OR site:fundacentro.gov.br'
    broad_query = f'{topic} segurança do trabalho prevenção orientação Brasil'
    errors=[]; found=[]
    for query in (official_query, broad_query):
        for engine in ('duckduckgo','bing'):
            try:
                results=_search_engine(query,engine)
                if results:
                    # Prefer official sources within each result set, then relevance/order.
                    results.sort(key=lambda item: 0 if _source_is_official(item.get('url','')) else 1)
                    found=results
                    break
                errors.append(f'{engine}: sem resultados úteis')
            except Exception as exc:
                errors.append(f'{engine}: {type(exc).__name__}: {exc}')
        if found:
            break
    if not found:
        raise RuntimeError('Não foi possível obter resultados online ('+'; '.join(errors)+')')
    enriched=[]
    for item in found:
        item['authority'] = 'Fonte oficial' if _source_is_official(item.get('url','')) else 'Fonte complementar — conferir antes de usar'
        try:
            body=_fetch_text(item['url'])
            item['excerpt']=_extract_relevant_sentences(body,topic) if body else item.get('snippet','')
        except Exception:
            item['excerpt']=item.get('snippet','')
        if item.get('excerpt') or item.get('snippet'):
            enriched.append(item)
        if len(enriched)>=5:
            break
    return enriched

def _extract_relevant_sentences(text: str, topic: str, limit: int=5) -> str:
    if not text: return ''
    terms=[x.lower() for x in re.findall(r'[A-Za-zÀ-ÿ0-9]{4,}',topic) if x.lower() not in {'para','sobre','como','com','das','dos','uma','um','tema','trabalho'}]
    sentences=re.split(r'(?<=[.!?])\s+',text)
    ranked=[]
    for sent in sentences:
        sent=' '.join(sent.split()).strip(' .')
        if len(sent)<65 or len(sent)>650: continue
        low=sent.lower(); score=sum(2 for term in terms if term in low)
        score+=sum(1 for word in ('preven','risco','seguran','prote','trabalhador','orienta','exposi','saúde','acidente','procedimento') if word in low)
        if score: ranked.append((score,sent))
    ranked.sort(key=lambda x:(-x[0],len(x[1])))
    chosen=[]; seen=set()
    for _,sent in ranked:
        key=sent.lower()
        if key in seen: continue
        seen.add(key); chosen.append(sent)
        if len(chosen)>=limit: break
    return ' '.join(chosen)[:2200]

def _draft_from_sources(topic: str, sources: list[dict]) -> str:
    evidence=[]
    for src in sources:
        excerpt=(src.get('excerpt') or src.get('snippet') or '').strip()
        if excerpt: evidence.append(excerpt)
    if not evidence: return ''
    # This is a source-grounded draft scaffold, not an assertion of automatic certification.
    guidance=' '.join(evidence)
    guidance=re.sub(r'\s+',' ',guidance).strip()
    guidance=guidance[:1750]
    return (f'POR QUE É IMPORTANTE?\nO tema “{topic}” exige atenção aos riscos específicos da atividade. As orientações abaixo foram preparadas a partir de referências públicas consultadas para este assunto; confirme sua aplicação ao procedimento local antes de orientar a equipe.\n\n'
            f'OBJETIVO\nReforçar a prevenção dos riscos relacionados a {topic.lower()} e orientar a equipe a seguir fontes oficiais e os procedimentos autorizados.\n\n'
            f'ORIENTAÇÕES BASEADAS NAS FONTES\n{guidance}\n\n'
            f'CONDUTA NO LOCAL DE TRABALHO\nAplique somente as medidas compatíveis com o procedimento da empresa, o treinamento recebido e as condições reais do local. Em caso de dúvida, exposição, incidente ou condição insegura, interrompa a atividade com segurança e comunique a liderança responsável. Não improvise instruções técnicas nem substitua rótulos, FDS ou procedimentos específicos.\n\n'
            f'MENSAGEM FINAL\nUse informações verificadas, siga o procedimento aplicável e comunique riscos antes que causem um acidente.')[:2600]

def init_topics_db() -> None:
    with closing(sqlite3.connect(TOPICS_DB_PATH)) as con:
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
    with closing(sqlite3.connect(TOPICS_DB_PATH)) as con:
        con.row_factory=sqlite3.Row
        return [dict(row) for row in con.execute(sql,args).fetchall()]

def read_state() -> dict:
    if not STATE_PATH.exists(): return default_state()
    try:
        data=json.loads(STATE_PATH.read_text(encoding='utf-8'))
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
        backup=STATE_PATH.with_suffix('.corrupt.json')
        try: STATE_PATH.replace(backup)
        except Exception: pass
        return default_state()


def write_state(state: dict) -> None:
    temp=STATE_PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(STATE_PATH)


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
    lines=[]; line=''
    for word in text.split():
        candidate=(line+' '+word).strip()
        if line and fitz.get_text_length(candidate,fontname=fontname,fontsize=fontsize)>width:
            lines.append(line); line=word
        else: line=candidate
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


def draw_content(page, content: str) -> bool:
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
                page.insert_text((x,y),line,fontname=font,fontsize=size,color=(0,0,0),overlay=True)
            return base < 8.1
    raise ValueError('O conteúdo ficou longo demais para o campo do formulário institucional. Resuma o texto antes de gerar o PDF.')


def _count_pdf_pages(path: Path) -> int:
    with fitz.open(path) as check_doc:
        return len(check_doc)


def centered_text(page, text: str, x0: float, x1: float, baseline: float, font='helv', size=8.0, min_size=6.0, color=(0,0,0)) -> float:
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
    page.insert_text((x,baseline),text,fontname=font,fontsize=cur,color=color,overlay=True)
    return cur


BLANK_ATTENDANCE_ROWS = 5
EXTENSION_SOURCE_PAGE = 2  # Institutional continuation page with 42 attendance rows.


def update_attendance_page_counter(page, page_number: int, total_pages: int) -> None:
    # The approved reference keeps page numbers only in the bottom-right footer.
    # The packaged clean template has its old footer values removed already.
    return None


def finalize_pdf_pagination_and_footer(doc, selected_unit: str) -> None:
    """Add the mandatory notice and physical page counter in the original footer band."""
    total_pages = len(doc)
    for index, page in enumerate(doc, start=1):
        centered_text(page, 'PROIBIDA a reprodução não autorizada', 22.3, 412.6, 813.1,
                      font='helv', size=7.0, min_size=6.0)
        centered_text(page, f'Página {index} de {total_pages}', 492.0, 568.0, 813.1,
                      font='helv', size=7.0, min_size=5.8)


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
        # Remove unused slots completely while leaving all rows above intact.
        page.add_redact_annot(fitz.Rect(x0, y0, x1, y1), fill=(1, 1, 1), cross_out=False)
        page.apply_redactions(images=0)
        page.draw_line((x0, y0), (x1, y0), color=(0, 0, 0), width=0.45, overlay=True)

    final_page_index = last_info['page']
    if final_page_index < len(doc) - 1:
        doc.delete_pages(final_page_index + 1, len(doc) - 1)
    return pages[:last_page_position + 1]


def build_pdf(payload: dict, employees: list[dict]) -> tuple[str, dict]:
    if not TEMPLATE_PATH.exists() or not LAYOUT_PATH.exists():
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
    supervisor_names=SUPERVISORS[shift]
    same_shift=[e for e in employees if e.get('shift')==shift]
    def employee_site(employee):
        return (canonical_site(employee.get('work_site'))
                or canonical_site(employee.get('operational_unit'))
                or canonical_site(employee.get('sector')))
    chosen=[e for e in same_shift if normalize(employee_site(e))==normalize(work_site)]
    if not chosen:
        raise ValueError('Nenhum colaborador corresponde ao turno e à unidade selecionados. O PDF não foi emitido para evitar uma lista incorreta. Confira os filtros e os cadastros.')
    layout=json.loads(LAYOUT_PATH.read_text(encoding='utf-8'))
    template_capacity=sum(len(p['rows']) for p in layout['pages'])
    required_rows=len(chosen)+BLANK_ATTENDANCE_ROWS
    doc=fitz.open(TEMPLATE_PATH)
    roster_pages=prepare_attendance_pages(doc, layout, required_rows)
    p=doc[0]
    title_size=10.7
    title_left, title_right = 101.0, 568.0
    while title_size>7.0 and fitz.get_text_length(topic.upper(),fontname='tibo',fontsize=title_size)>title_right-title_left-4.0:
        title_size-=0.3
    centered_text(p,topic.upper(),title_left,title_right,105.1,font='tibo',size=title_size,min_size=7.0)
    shrunk=draw_content(p,content)

    # Fixed fields in the same cells as the supplied institutional document.
    centered_text(p,work_site,504.0,568.0,318.1,font='helv',size=8.0,min_size=6.0)
    turn_display={'DIURNO':'MANHÃ / DIURNO','VESPERTINO':'TARDE / VESPERTINO','NOTURNO':'MADRUGADA / NOTURNO'}[shift]
    centered_text(p,turn_display,100.5,206.5,341.8,font='helv',size=7.8,min_size=6.0)
    centered_text(p,duration,402.8,448.6,341.8,font='helv',size=8.0,min_size=6.0)
    centered_text(p,display_date,504.0,568.0,341.8,font='helv',size=8.0,min_size=6.0)
    # Only the responsible people for the selected shift are printed. Other columns remain blank for manual entry.
    supervisor_text=' e '.join(supervisor_names)
    centered_text(p,supervisor_text,51.7,270.0,378.4,font='helv',size=7.7,min_size=6.2)

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
            centered_text(pg,str(row_number),float(columns['number'][0]),float(columns['number'][1]),baseline,
                          font='helv',size=7.8,min_size=6.0)
            if row_number<=len(chosen):
                employee=chosen[row_number-1]
                name=str(employee.get('name','')).strip()
                name_size=8.2
                name_x=float(columns['name'][0])+1.0 + 4.0 * fitz.get_text_length(' ', fontname='helv', fontsize=name_size)
                name_right=float(columns['name'][1])-2.0
                while name_size>=5.8 and fitz.get_text_length(name,fontname='helv',fontsize=name_size)>name_right-name_x:
                    name_size-=0.25
                if fitz.get_text_length(name,fontname='helv',fontsize=name_size)>name_right-name_x:
                    raise ValueError(f'O nome “{name}” é longo demais para caber na linha do modelo institucional. Ajuste o cadastro ou utilize um nome abreviado aprovado pela liderança.')
                pg.insert_text((name_x,baseline),name,fontname='helv',fontsize=name_size,color=(0,0,0),overlay=True)
            # Area and unit are prefilled on employee and extraordinary rows, exactly as requested.
            centered_text(pg,'LIMPEZA',float(columns['area'][0]),float(columns['area'][1]),baseline,font='helv',size=7.8,min_size=5.8)
            centered_text(pg,work_site,float(columns['unit'][0]),float(columns['unit'][1]),baseline,font='helv',size=7.8,min_size=5.8)

    finalize_pdf_pagination_and_footer(doc,work_site)
    emission_id=uuid.uuid4().hex[:8]
    generated_at=datetime.now()
    date_label=display_date.replace('/','-') if display_date else 'SEM_DATA'
    fname=f'DDS_{date_label}_{slugify(topic)}_{shift.lower()}_{generated_at.strftime("%H%M%S")}_{emission_id}.pdf'
    target=OUTPUT_DIR/fname
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
          'duration':duration,'supervisors':supervisor_names,'participants':len(chosen),'file':fname,
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
    return jsonify({'status':'ok','app':'Gerador de DDS','version':'1.2.2-web-ready' if HOSTED_MODE else '1.2.2-local'})

@app.get('/api/state')
def state_endpoint():
    state=read_state()
    return jsonify({'employees':state['employees'],'history':state['history'],'settings':state['settings'],
                    'unit_code':UNIT_CODE,'work_sites':WORK_SITES,'units':WORK_SITES,
                    'shifts':SHIFTS,'supervisors':SUPERVISORS,'unit_supervisors':UNIT_SUPERVISORS,'capacity':template_capacity(),'blank_attendance_rows':BLANK_ATTENDANCE_ROWS,'capacity_mode':'dynamic'})

def backup_payload() -> dict:
    state = read_state()
    custom_topics = [row for row in topic_rows() if row.get('source') == 'cadastrado pelo usuário']
    return {
        'format': 'gerador-dds-backup',
        'backup_version': 1,
        'app_version': '1.2.2',
        'exported_at': datetime.now().isoformat(timespec='seconds'),
        'state': state,
        'custom_topics': custom_topics,
        'scope': 'Cadastros, configurações, histórico e temas criados pelo usuário. Os PDFs já gerados permanecem na pasta local outputs e não são incluídos neste arquivo.'
    }


@app.get('/api/backup-data')
def export_backup_data():
    return jsonify(backup_payload())


@app.post('/api/restore-backup')
def restore_backup_data():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({'error': 'Arquivo de backup inválido.'}), 400
    if body.get('format') != 'gerador-dds-backup' or body.get('backup_version') != 1:
        return jsonify({'error': 'Arquivo de backup inválido ou de versão não compatível.'}), 400
    imported_state = body.get('state')
    custom_topics = body.get('custom_topics', [])
    if not isinstance(imported_state, dict) or not isinstance(imported_state.get('employees', []), list):
        return jsonify({'error': 'O backup não contém uma relação válida de colaboradores.'}), 400
    if not isinstance(imported_state.get('history', []), list) or not isinstance(imported_state.get('settings', {}), dict):
        return jsonify({'error': 'O histórico ou as configurações do backup são inválidos.'}), 400
    if not isinstance(custom_topics, list) or len(custom_topics) > 5000:
        return jsonify({'error': 'A lista de temas do backup é inválida ou excede o limite permitido.'}), 400
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
    # Preserve a recoverable snapshot before replacing any live data.
    backups_dir = DATA_DIR / 'backups'
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:4]
    (backups_dir / f'antes_restauracao_{stamp}.json').write_text(
        json.dumps(backup_payload(), ensure_ascii=False, indent=2), encoding='utf-8')
    init_topics_db()
    try:
        with closing(sqlite3.connect(TOPICS_DB_PATH)) as con:
            con.execute('BEGIN')
            con.execute("DELETE FROM topics WHERE source='cadastrado pelo usuário'")
            for title, category, objective, content, created_at, updated_at in normalized_topics:
                con.execute('''INSERT OR IGNORE INTO topics(title,category,objective,content,source,created_at,updated_at)
                               VALUES(?,?,?,?,?,?,?)''',
                            (title, category, objective, content, 'cadastrado pelo usuário', created_at, updated_at))
            con.commit()
    except Exception:
        return jsonify({'error': 'Não foi possível restaurar os temas. O estado atual foi preservado em uma cópia de segurança local.'}), 500
    restored_state = {
        'employees': employees,
        'history': raw_history[:100],
        'settings': imported_state.get('settings', default_state()['settings'])
    }
    write_state(restored_state)
    return jsonify({'ok': True, 'employees': len(employees), 'history': len(restored_state['history']),
                    'custom_topics': len(normalized_topics), 'message': 'Backup restaurado. Foi salva uma cópia dos dados anteriores neste computador.'})

@app.post('/api/research-topic')
def research_topic():
    body=request.get_json(silent=True) or {}
    title=str(body.get('title','')).strip()
    if not title: return jsonify({'error':'Informe o tema que deseja pesquisar.'}),400
    if len(title)>140: return jsonify({'error':'O tema deve ter até 140 caracteres.'}),400
    init_topics_db()
    # A pesquisa desta ação precisa de evidência obtida agora; a biblioteca local
    # não é usada como substituta silenciosa quando as fontes online falham.
    try:
        sources=_web_search(title)
        content=_draft_from_sources(title,sources)
        if content and sources:
            category='Pesquisa na internet — revisar antes de usar'
            objective=f'Reforçar práticas seguras relacionadas a {title.lower()}, com base nas fontes públicas listadas.'
            now=datetime.now().isoformat(timespec='seconds')
            source_value='WEB-PESQUISADO:'+json.dumps(sources,ensure_ascii=False)
            with closing(sqlite3.connect(TOPICS_DB_PATH)) as con:
                existing=con.execute('SELECT id FROM topics WHERE title=? COLLATE NOCASE',(title,)).fetchone()
                if existing:
                    con.execute('UPDATE topics SET category=?,objective=?,content=?,source=?,updated_at=? WHERE id=?',(category,objective,content,source_value,now,existing[0]))
                else:
                    con.execute('INSERT INTO topics(title,category,objective,content,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(title,category,objective,content,source_value,now,now))
                con.commit()
            return jsonify({'ok':True,'mode':'online','title':title,'category':category,'objective':objective,'content':content,'sources':sources,'message':'Rascunho de pesquisa preparado a partir de fontes online. Confira as fontes e revise o conteúdo antes de usar; fontes complementares não são oficiais.'})
    except Exception as exc:
        app.logger.warning('Pesquisa online de DDS indisponível: %s',exc)
    # This action is explicitly a live-source validation workflow. Never label stale cache
    # or a generic offline draft as a successful research result.
    return jsonify({
        'ok': False,
        'error': 'Não foi possível obter resultados úteis na internet nesta tentativa. Tente novamente com palavras-chave mais específicas.',
        'search_required': True,
        'sources_expected': ['Ministério do Trabalho e Emprego (MTE)', 'Fundacentro', 'Anvisa (quando pertinente)']
    }), 503



@app.get('/api/topics')
def list_topics():
    rows=topic_rows(request.args.get('q',''),request.args.get('category',''))
    categories=sorted({row['category'] for row in topic_rows()},key=str.casefold)
    return jsonify({'topics':rows,'categories':categories,'count':len(rows),'offline':True})

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
    now=datetime.now().isoformat(timespec='seconds')
    init_topics_db()
    try:
        with closing(sqlite3.connect(TOPICS_DB_PATH)) as con:
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
    now=datetime.now().isoformat(timespec='seconds')
    init_topics_db()
    try:
        with closing(sqlite3.connect(TOPICS_DB_PATH)) as con:
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
    with closing(sqlite3.connect(TOPICS_DB_PATH)) as con:
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
        backups=DATA_DIR/'backups'; backups.mkdir(parents=True,exist_ok=True)
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
            with open(DATA_DIR/'dds_generation.log','a',encoding='utf-8') as log:
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
                if not matching:
                    skipped.append({'topic':topic_item['topic'],'shift':shift,'unit':unit,'reason':'Nenhum colaborador cadastrado para esta combinação.'})
                    continue
                payload={**body,**topic_item,'shift':shift,'work_site':unit,'unit':unit,'sector':'LIMPEZA','duration':'01:00','date':iso_date}
                try:
                    fname,meta=build_pdf(payload,state['employees'])
                    temp_files.append((OUTPUT_DIR/fname,meta))
                    generated.append(meta)
                except Exception as exc:
                    errors.append({'topic':topic_item['topic'],'shift':shift,'unit':unit,'error':str(exc)})
    if not temp_files:
        return jsonify({'error':'Nenhum PDF foi gerado. Confira colaboradores, turnos e unidades selecionados.','skipped':skipped,'failures':errors}),400
    # Merge all selected topic/shift/unit documents into one downloadable PDF.
    stamp=datetime.now(); combined_id=uuid.uuid4().hex[:8]
    date_label=iso_date or 'SEM_DATA'
    combined_name=f'DDS_MASSA_{date_label}_{stamp.strftime("%H%M%S")}_{combined_id}.pdf'
    combined_path=OUTPUT_DIR/combined_name
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
    path=OUTPUT_DIR/safe
    if not path.exists() or path.suffix.lower()!='.pdf': abort(404)
    return send_from_directory(OUTPUT_DIR,safe,mimetype='application/pdf',as_attachment=False,download_name=safe)

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
