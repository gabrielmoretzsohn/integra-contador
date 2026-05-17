"""
relatorios.py - Geracao de relatorios consolidados
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger("integra_contador.relatorios")


def gerar_relatorio_ciclo(resultados: List[Dict], diretorio_saida: str = "relatorios") -> str:
    Path(diretorio_saida).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_br   = datetime.now().strftime("%d/%m/%Y %H:%M")

    caminho_json = Path(diretorio_saida) / f"ciclo_{timestamp}.json"
    caminho_json.write_text(
        json.dumps(resultados, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8"
    )

    caminho_html = Path(diretorio_saida) / f"relatorio_{timestamp}.html"
    html = _gerar_html(resultados, data_br)
    caminho_html.write_text(html, encoding="utf-8")

    logger.info(f"Relatorio gerado: {caminho_html}")
    return str(caminho_html)


def _gerar_html(resultados: List[Dict], data_br: str) -> str:
    total  = len(resultados)
    ok     = sum(1 for r in resultados if _tudo_ok(r))
    falhas = total - ok

    # ── Linhas da tabela principal ────────────────────────────────────────────
    linhas = ""
    for r in resultados:
        nome   = r.get("nome", r.get("numero", ""))
        numero = r.get("numero", "")
        sitfis      = r.get("sitfis",      {})
        caixa       = r.get("caixa_postal", {})
        dctfweb      = r.get("dctfweb",      {})
        mit          = r.get("mit",          {})
        parcelamento = r.get("parcelamento", {})

        def celula(res, label=""):
            if res.get("pulado"):
                return '<td class="pulado">—<br><small>nao solicitado</small></td>'
            if res.get("sucesso") is True:
                pdf = res.get("arquivo_pdf", "")
                link = f'<a href="{Path(pdf).name}" target="_blank">Abrir</a>' if pdf else ""
                return f'<td class="ok">OK {link}</td>'
            if res.get("sucesso") is False:
                erro = res.get("erro", "Erro")[:80]
                return f'<td class="erro">Falhou<br><small>{erro}</small></td>'
            return '<td class="pulado">—</td>'

        def celula_dctfweb(res):
            if res.get("pulado"):
                return '<td class="pulado">—</td>'
            if res.get("sucesso") is False and res.get("erro"):
                return f'<td class="erro">Erro<br><small>{res.get("erro","")[:60]}</small></td>'
            transmitida = res.get("transmitida")
            periodo     = res.get("periodo", "")
            categoria   = res.get("categoria", "")
            pdf         = res.get("arquivo_pdf", "")
            link = f' <a href="{Path(pdf).name}" target="_blank">PDF</a>' if pdf else ""
            if transmitida is True:
                return f'<td class="ok">Transmitida{link}<br><small>{periodo} | {categoria}</small></td>'
            if transmitida is False:
                return f'<td class="erro">Nao transmitida<br><small>{periodo}</small></td>'
            return '<td class="pulado">—</td>'


        def celula_parcelamento(res):
            if res.get("pulado"):
                return '<td class="pulado">—</td>'
            if res.get("sucesso") is False:
                return f'<td class="erro">Erro<br><small>{res.get("erro","")[:60]}</small></td>'
            if res.get("sucesso") is True:
                total = res.get("total", 0)
                if total > 0:
                    return f'<td class="erro">Sim ({total})</td>'
                return '<td class="ok">Sem parcelamentos</td>'
            return '<td class="pulado">—</td>'

        n_msg = caixa.get("total_mensagens", "—") if caixa.get("sucesso") else "—"

        # Situacao fiscal do SITFIS
        sit = sitfis.get("situacao_fiscal", "") if sitfis.get("sucesso") else ""
        debito = sitfis.get("possui_debito")
        if sit or debito is not None:
            if debito is True:
                cel_sit = '<td class="erro">IRREGULAR<br><small>Possui debito</small></td>'
            elif debito is False:
                cel_sit = '<td class="ok">REGULAR</td>'
            elif sit:
                cel_sit = f'<td>{sit}</td>'
            else:
                cel_sit = '<td class="pulado">—</td>'
        else:
            cel_sit = '<td class="pulado">—</td>'

        # Simples Nacional
        simples = r.get("simples", {})
        def celula_mit(res):
            if res.get("pulado"):
                return '<td class="pulado">—</td>'
            if res.get("sucesso") is False and res.get("erro"):
                return f'<td class="erro">Erro<br><small>{res.get("erro","")[:50]}</small></td>'
            if res.get("sucesso") is True:
                transmitida = res.get("transmitida")
                situacao    = res.get("situacao", "")
                periodo     = res.get("periodo", "")
                pdf         = res.get("arquivo_pdf", "")
                link = f' <a href="{Path(pdf).name}" target="_blank">PDF</a>' if pdf else ""
                if transmitida:
                    return f'<td class="ok">Transmitida{link}<br><small>{periodo} | {situacao[:20]}</small></td>'
                return f'<td class="erro">Nao transmitida<br><small>{periodo} | {situacao[:30]}</small></td>'
            return '<td class="pulado">—</td>'

        def celula_simples(res):
            if res.get("pulado"):
                motivo = res.get("motivo", "")
                if motivo:
                    return f'<td class="pulado"><small>{motivo[:40]}</small></td>'
                return '<td class="pulado">—</td>'
            if res.get("nao_simples"):
                return '<td class="pulado"><small>Nao optante SN</small></td>'
            if res.get("sucesso") is True:
                transmitida = res.get("transmitida")
                periodo     = res.get("periodo", "")
                total       = res.get("total", 0)
                recibo      = res.get("ultimo_recibo", "")[:16]
                data        = res.get("ultima_data", "")[:16]
                if transmitida:
                    detalhe = recibo or data or str(total) + " decl."
                    return f'<td class="ok">Transmitida<br><small>{periodo} | {detalhe}</small></td>'
                return f'<td class="erro">Nao transmitida<br><small>{periodo}</small></td>'
            if res.get("sucesso") is False:
                return f'<td class="erro">Erro<br><small>{res.get("erro","")[:50]}</small></td>'
            return '<td class="pulado">—</td>'


        linhas += f"""<tr>
            <td><strong>{nome}</strong><br><small>{_fmt_cnpj(numero)}</small></td>
            {celula(sitfis)}
            {cel_sit}
            {celula(caixa)}
            <td>{n_msg}</td>
            {celula_dctfweb(dctfweb)}
            {celula_mit(mit)}
            {celula_simples(simples)}
            {celula_parcelamento(parcelamento)}
        </tr>"""

    # ── Tabela de falhas ──────────────────────────────────────────────────────
    falhas_rows = ""
    for r in resultados:
        nome   = r.get("nome", r.get("numero", ""))
        numero = r.get("numero", "")
        for chave, label in [("sitfis", "SITFIS"), ("cnd", "CND Federal"), ("caixa_postal", "Caixa Postal")]:
            res = r.get(chave, {})
            if res.get("sucesso") is False and not res.get("pulado"):
                erro = res.get("erro", "Motivo nao identificado")
                falhas_rows += f"""<tr>
                    <td>{_fmt_cnpj(numero)}</td>
                    <td>{nome}</td>
                    <td>{label}</td>
                    <td>{erro}</td>
                </tr>"""

    tabela_falhas = ""
    if falhas_rows:
        tabela_falhas = f"""
        <h2 style="color:#b91c1c;margin-top:32px">Empresas com Falha na Geracao</h2>
        <table>
          <thead><tr>
            <th>CNPJ</th><th>Nome</th><th>Servico</th><th>Motivo da Falha</th>
          </tr></thead>
          <tbody>{falhas_rows}</tbody>
        </table>
        <p style="font-size:12px;color:#666;margin-top:8px">
          <strong>Causa mais comum:</strong> HTTP 304 indica que ja existe um relatorio
          gerado hoje para este CNPJ. Execute novamente amanha, ou verifique se a
          procuracao eletronica do SITFIS esta ativa no e-CAC para esta empresa.
        </p>"""
    else:
        tabela_falhas = """<p style="color:#16a34a;font-weight:bold;margin-top:24px">
            Todas as consultas foram concluidas com sucesso.</p>"""

    # ── Nota sobre CND ────────────────────────────────────────────────────────
    nota_cnd = """
    <div style="background:#fffbeb;border:1px solid #fbbf24;border-radius:6px;
                padding:12px 16px;margin-top:24px;font-size:12px;color:#78350f">
      <strong>Nota sobre CND Federal:</strong> A API SERPRO (Integra Contador) nao
      disponibiliza um endpoint separado para CND. O PDF gerado pelo servico
      <em>CND Federal</em> e o proprio Relatorio de Situacao Fiscal (SITFIS), que
      contem as informacoes de debitos e situacao de regularidade fiscal —
      o mesmo documento emitido no e-CAC ao clicar em "Emitir Certidao".
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<title>Integra Contador — {data_br}</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0 }}
body {{ font-family:'Segoe UI',Arial,sans-serif; background:#f4f6fa; color:#222; padding:20px }}
.header {{ background:linear-gradient(135deg,#1a3a6c,#2563eb); color:white;
           padding:20px 28px; border-radius:10px; margin-bottom:20px }}
.header h1 {{ font-size:20px; font-weight:700 }}
.header p  {{ opacity:.8; font-size:12px; margin-top:4px }}
.cards {{ display:flex; gap:14px; margin-bottom:20px; flex-wrap:wrap }}
.card {{ background:white; border-radius:8px; padding:16px 22px; flex:1;
         min-width:120px; box-shadow:0 1px 4px rgba(0,0,0,.08) }}
.card .val {{ font-size:28px; font-weight:700 }}
.card .lbl {{ font-size:11px; color:#666; margin-top:2px }}
.verde {{ color:#16a34a }} .vermelho {{ color:#dc2626 }} .azul {{ color:#1a3a6c }}
table {{ width:100%; border-collapse:collapse; background:white; border-radius:10px;
         overflow:hidden; box-shadow:0 1px 4px rgba(0,0,0,.08); margin-top:8px }}
th {{ background:#1a3a6c; color:white; padding:10px 14px; text-align:left; font-size:12px }}
td {{ padding:10px 14px; border-bottom:1px solid #eee; font-size:12px; vertical-align:top }}
tr:hover {{ background:#f8faff }}
.ok     {{ color:#16a34a }}
.erro   {{ color:#dc2626 }}
.pulado {{ color:#aaa }}
a {{ color:#2563eb; text-decoration:none }}
a:hover {{ text-decoration:underline }}
h2 {{ font-size:15px; margin-top:24px; margin-bottom:8px }}
.footer {{ text-align:center; color:#aaa; font-size:11px; margin-top:28px }}
</style>
</head>
<body>
<div class="header">
  <h1>Integra Contador — Relatorio de Consulta Fiscal</h1>
  <p>Gerado em {data_br} &nbsp;|&nbsp; Fonte: API SERPRO / Receita Federal do Brasil</p>
</div>

<div class="cards">
  <div class="card"><div class="val azul">{total}</div><div class="lbl">Contribuintes</div></div>
  <div class="card"><div class="val verde">{ok}</div><div class="lbl">Todos OK</div></div>
  <div class="card"><div class="val vermelho">{falhas}</div><div class="lbl">Com Falhas</div></div>
</div>

<h2>Resultado por Empresa</h2>
<table>
  <thead><tr>
    <th>Empresa</th>
    <th>Diagnostico Fiscal (SITFIS)</th>
    <th>Situacao Fiscal</th>
    <th>Caixa Postal</th>
    <th>Msgs</th>
    <th>DCTFWeb</th>
    <th>Simples (PGDAS)</th>
    <th>Parcelamento</th>
  </tr></thead>
  <tbody>{linhas}</tbody>
</table>

{tabela_falhas}
{nota_cnd}

<div class="footer">
  Integra Contador — SERPRO / Receita Federal &nbsp;|&nbsp; Gerado automaticamente
</div>
</body>
</html>"""


def _tudo_ok(r):
    for chave in ["sitfis", "cnd", "caixa_postal"]:
        res = r.get(chave, {})
        if res.get("sucesso") is False and not res.get("pulado"):
            return False
    return True


def _fmt_cnpj(n):
    n = str(n).strip().zfill(14)
    if len(n) == 14:
        return f"{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}"
    if len(n) == 11:
        return f"{n[:3]}.{n[3:6]}.{n[6:9]}-{n[9:]}"
    return n
