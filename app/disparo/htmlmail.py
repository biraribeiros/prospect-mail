"""HTML de email escrito por você no editor: limpeza (só marcas seguras), versão em texto puro e moldura do email.

Nada de imagem, script, estilo livre, formulário ou iframe: o que não está na lista é descartado. Links só http, https ou
mailto. Isso protege quem recebe e mantém o email com cara de mensagem pessoal (menos chance de cair no spam).
"""
import re
from html import escape
from html.parser import HTMLParser

PERMITIDAS = {"p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li", "blockquote", "a", "h2", "h3", "hr", "div"}
VAZIAS = {"br", "hr"}
IGNORAR_CONTEUDO = {"script", "style", "iframe", "object", "embed", "head", "title", "noscript", "svg", "template"}
COM_ALINHAMENTO = {"p", "div", "h2", "h3", "li", "blockquote"}
ALINHA_RE = re.compile(r"(?:^|;)\s*text-align\s*:\s*(left|center|right|justify)\s*(?:;|$)", re.I)  # do style, só o alinhamento
FECHAM_P = {"p", "h2", "h3", "ul", "ol", "blockquote", "hr", "div"}  # em HTML, estes encerram um <p> aberto
LINK_RE = re.compile(r"(https?://|mailto:)[^\s\"'<>]+", re.I)
MAX_BYTES = 200_000
NL = chr(10)


class _Limpa(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.pilha: list[str] = []
        self.ignorar = 0

    def handle_starttag(self, tag, attrs):
        if tag in IGNORAR_CONTEUDO:
            self.ignorar += 1
            return
        if self.ignorar or tag not in PERMITIDAS:
            return
        a = dict(attrs)
        if tag in FECHAM_P and self.pilha and self.pilha[-1] == "p":
            self.out.append("</p>")
            self.pilha.pop()
        if tag == "li" and "li" in self.pilha:  # <li> sem fechar o anterior: fecha o item aberto da mesma lista
            ult_li = len(self.pilha) - 1 - self.pilha[::-1].index("li")
            ult_lista = max((i for i, t in enumerate(self.pilha) if t in ("ul", "ol")), default=-1)
            if ult_li > ult_lista:
                while len(self.pilha) > ult_li:
                    self.out.append(f"</{self.pilha.pop()}>")
        if tag == "a":
            href = (a.get("href") or "").strip()
            if "a" in self.pilha or not LINK_RE.fullmatch(href):
                return  # link inseguro ou aninhado: some a marca, o texto fica
            self.out.append(f'<a href="{escape(href, quote=True)}">')
            self.pilha.append("a")
            return
        extra = ""
        if tag in COM_ALINHAMENTO:
            m = ALINHA_RE.search(a.get("style") or "")
            if m:
                extra = f' style="text-align:{m.group(1).lower()}"'
        self.out.append(f"<{tag}{extra}>")
        if tag not in VAZIAS:
            self.pilha.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in VAZIAS:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in IGNORAR_CONTEUDO:
            self.ignorar = max(0, self.ignorar - 1)
            return
        if self.ignorar or tag not in self.pilha:
            return
        while self.pilha:  # fecha o que ficou aberto por dentro
            t = self.pilha.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.ignorar:
            self.out.append(escape(data, quote=False))

    def fechar(self) -> str:
        while self.pilha:
            self.out.append(f"</{self.pilha.pop()}>")
        return "".join(self.out)


def sanitize_html(html: str) -> str:
    p = _Limpa()
    p.feed(html[:MAX_BYTES])
    p.close()
    return re.sub(r"(<p>\s*(<br>)?\s*</p>\s*){2,}", "<p></p>", p.fechar()).strip()


class _Texto(HTMLParser):
    PARAGRAFOS = {"p", "div", "h2", "h3", "blockquote", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.t = ""
        self.links: list[tuple[str, int]] = []

    def _garante(self, n: int) -> None:
        """Garante n quebras de linha seguidas no fim (não duplica as que já existem)."""
        if self.t:
            self.t += NL * max(0, n - (len(self.t) - len(self.t.rstrip(NL))))

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.t += NL
        elif tag in self.PARAGRAFOS or tag in ("ul", "ol"):
            self._garante(2)
        elif tag == "li":
            self._garante(1)
            self.t += "- "
        elif tag == "a":
            self.links.append((dict(attrs).get("href") or "", len(self.t)))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self.PARAGRAFOS or tag in ("ul", "ol"):
            self._garante(2)
        elif tag == "a" and self.links:
            href, ini = self.links.pop()
            texto = self.t[ini:].strip()
            if href and not href.lower().startswith("mailto:") and href.rstrip("/") != texto.rstrip("/"):
                self.t += f" ({href})"

    def handle_data(self, data):
        self.t += data


def html_to_text(html: str) -> str:
    p = _Texto()
    p.feed(html)
    p.close()
    t = p.t.replace(chr(160), " ")
    t = re.sub(r"[ \t]+\n", NL, t)
    t = re.sub(r"\n{3,}", NL * 2, t)
    return t.strip()


def wrap_html(body: str) -> str:
    """Moldura simples do email: fonte comum, sem imagem nem cor de fundo."""
    return ('<!doctype html><html><head><meta charset="utf-8"></head>'
            '<body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;color:#222222">'
            f"{body}</body></html>")
