"""
engine.py — Motor de consultas que usa os módulos Python existentes
Roda em thread separada e emite eventos WebSocket em tempo real
"""

import os, json, time, logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger('integra.engine')


def executar_consulta(app, consulta_id: int, config: dict, servicos: list,
                       cnpjs: list, periodo: dict):
    """
    Executa todas as consultas selecionadas usando os módulos Python prontos.
    Roda em thread separada. Emite eventos via socketio.
    """
    with app.app_context():
        from .. import db, socketio
        from ..models import Consulta, Empresa, StatusObrigacao, Alerta

        consulta = db.session.get(Consulta, consulta_id)
        if not consulta:
            return

        consulta.status = 'rodando'
        db.session.commit()

        def log(msg, nivel='info'):
            """Salva no banco E emite via WebSocket em tempo real."""
            consulta.log = (consulta.log or '') + msg + '\n'
            db.session.commit()
            socketio.emit('log', {'cid': consulta_id, 'msg': msg, 'nivel': nivel},
                          namespace='/')

        def emit_progresso(empresa_nome, passo, total):
            socketio.emit('progresso', {
                'cid': consulta_id,
                'empresa': empresa_nome,
                'passo': passo,
                'total': total,
            }, namespace='/')

        try:
            # ── Importa módulos Python prontos ────────────────────────────────
            from ..services.autenticacao import SerproAuth
            from ..services.cliente_api import ClienteIntegra
            from ..services.servicos import (
                consultar_diagnostico_fiscal,
                listar_mensagens_caixa_postal,
                ler_mensagens_nao_lidas_pdf,
            )
            from ..services.servicos_dctfweb import consultar_situacao_dctfweb, consultar_mit
            from ..services.servicos_simples import consultar_declaracoes_pgdas
            from ..services.servicos_parcelamento import consultar_parcelamentos

            # ── Autenticação SERPRO ───────────────────────────────────────────
            log('=' * 50)
            log(f'Iniciando consulta #{consulta_id}')
            log(f'Servicos: {", ".join(servicos)}')
            log(f'Periodo: {periodo.get("mes")}/{periodo.get("ano")}')
            log('=' * 50)
            log('Autenticando com certificado digital...')

            cert_path = config.get('cert_path', '')
            if not cert_path or not Path(cert_path).exists():
                log('ERRO: Certificado digital não encontrado. Configure nas Configurações.', 'erro')
                consulta.status = 'erro'
                db.session.commit()
                socketio.emit('concluida', {'cid': consulta_id, 'status': 'erro'})
                return

            auth = SerproAuth(
                consumer_key      = config['consumer_key'],
                consumer_secret   = config['consumer_secret'],
                caminho_certificado = cert_path,
                senha_certificado = config['cert_senha'],
                cnpj_contratante  = config['cnpj_escritorio'],
            )
            cliente = ClienteIntegra(
                auth=auth,
                api_base_url='https://gateway.apiserpro.serpro.gov.br/integra-contador/v1',
            )
            log('Autenticação OK', 'ok')

            # ── Pasta de saída (dentro do servidor) ───────────────────────────
            rel_dir = app.config['RELATORIOS_FOLDER']
            Path(rel_dir).mkdir(parents=True, exist_ok=True)

            cnpj_esc = config['cnpj_escritorio']
            mes      = periodo.get('mes', datetime.now().strftime('%m'))
            ano      = periodo.get('ano', datetime.now().strftime('%Y'))

            empresas = Empresa.query.filter(
                Empresa.cnpj.in_(cnpjs), Empresa.ativa == True
            ).order_by(Empresa.nome).all()

            total    = len(empresas)
            sucessos = 0
            erros    = 0

            for idx, emp in enumerate(empresas, 1):
                log(f'\n[{idx}/{total}] {emp.nome} ({emp.cnpj_fmt})')
                emit_progresso(emp.nome, idx, total)
                res = {}

                # SITFIS
                if 'sitfis' in servicos:
                    log(f'  SITFIS...')
                    try:
                        r = consultar_diagnostico_fiscal(
                            cliente, cnpj_esc, emp.cnpj, 2, '', rel_dir)
                        res['sitfis'] = r
                        if r.get('sucesso'):
                            sit = r.get('situacao_fiscal', '')
                            deb = ' | DEBITO IDENTIFICADO' if r.get('possui_debito') else ''
                            log(f'  SITFIS OK{deb}', 'ok')
                        else:
                            log(f'  SITFIS FALHOU: {r.get("erro","")[:80]}', 'erro')
                    except Exception as e:
                        res['sitfis'] = {'sucesso': False, 'erro': str(e)}
                        log(f'  SITFIS ERRO: {e}', 'erro')
                    time.sleep(2)

                # Caixa Postal
                if 'caixa' in servicos:
                    log(f'  Caixa Postal...')
                    try:
                        r = listar_mensagens_caixa_postal(cliente, cnpj_esc, emp.cnpj, 2)
                        res['caixa'] = r
                        n = r.get('total_mensagens', 0)
                        log(f'  Caixa Postal OK — {n} mensagem(ns)', 'ok')
                    except Exception as e:
                        res['caixa'] = {'sucesso': False, 'erro': str(e)}
                        log(f'  Caixa ERRO: {e}', 'erro')
                    time.sleep(1)

                # Caixa Postal — PDF não lidas
                if 'caixa_nao_lidas' in servicos:
                    log(f'  Caixa Postal (PDF não lidas)...')
                    try:
                        r = ler_mensagens_nao_lidas_pdf(cliente, cnpj_esc, emp.cnpj, 2, rel_dir)
                        res['caixa_nao_lidas'] = r
                        n = r.get('total_nao_lidas', 0)
                        log(f'  PDF Caixa OK — {n} nao lida(s)', 'ok')
                    except Exception as e:
                        res['caixa_nao_lidas'] = {'sucesso': False, 'erro': str(e)}
                        log(f'  Caixa PDF ERRO: {e}', 'erro')
                    time.sleep(1)

                # DCTFWeb
                if 'dctfweb' in servicos:
                    log(f'  DCTFWeb {mes}/{ano}...')
                    try:
                        r = consultar_situacao_dctfweb(
                            cliente, cnpj_esc, emp.cnpj, 2, ano, mes,
                            diretorio_saida=rel_dir)
                        res['dctfweb'] = r
                        t = 'TRANSMITIDA' if r.get('transmitida') else 'NAO TRANSMITIDA'
                        nivel = 'ok' if r.get('transmitida') else 'aviso'
                        log(f'  DCTFWeb {t}', nivel)
                    except Exception as e:
                        res['dctfweb'] = {'sucesso': False, 'erro': str(e)}
                        log(f'  DCTFWeb ERRO: {e}', 'erro')
                    time.sleep(2)

                # MIT
                if 'mit' in servicos:
                    log(f'  MIT {mes}/{ano}...')
                    try:
                        r = consultar_mit(cliente, cnpj_esc, emp.cnpj, 2, ano, mes, rel_dir)
                        res['mit'] = r
                        t = 'TRANSMITIDO' if r.get('transmitida') else 'NAO TRANSMITIDO'
                        log(f'  MIT {t}', 'ok' if r.get('transmitida') else 'aviso')
                    except Exception as e:
                        res['mit'] = {'sucesso': False, 'erro': str(e)}
                        log(f'  MIT ERRO: {e}', 'erro')
                    time.sleep(2)

                # PGDAS-D
                if 'simples' in servicos:
                    log(f'  PGDAS-D {mes}/{ano}...')
                    try:
                        r = consultar_declaracoes_pgdas(
                            cliente, cnpj_esc, emp.cnpj, 2, ano, mes)
                        res['simples'] = r
                        if r.get('nao_simples'):
                            log(f'  PGDAS: nao optante do Simples', 'info')
                        else:
                            t = 'TRANSMITIDA' if r.get('transmitida') else 'NAO TRANSMITIDA'
                            log(f'  PGDAS {t}', 'ok' if r.get('transmitida') else 'aviso')
                    except Exception as e:
                        res['simples'] = {'sucesso': False, 'erro': str(e)}
                        log(f'  PGDAS ERRO: {e}', 'erro')
                    time.sleep(2)

                # Parcelamentos
                if 'parcelamento' in servicos:
                    log(f'  Parcelamentos...')
                    try:
                        r = consultar_parcelamentos(cliente, cnpj_esc, emp.cnpj, 2)
                        res['parcelamento'] = r
                        n = r.get('total', 0)
                        msg = f'SIM ({n} ativo(s))' if n > 0 else 'Sem parcelamentos'
                        log(f'  Parcelamento: {msg}', 'aviso' if n > 0 else 'ok')
                    except Exception as e:
                        res['parcelamento'] = {'sucesso': False, 'erro': str(e)}
                        log(f'  Parcelamento ERRO: {e}', 'erro')
                    time.sleep(1)

                # ── Salva status no banco ─────────────────────────────────────
                _salvar_status(db, emp, res, mes, ano, consulta_id)
                _gerar_alertas(db, emp, res, mes, ano)

                tem_erro = any(v.get('sucesso') is False
                               for v in res.values() if isinstance(v, dict))
                if tem_erro:
                    erros += 1
                else:
                    sucessos += 1

                socketio.emit('empresa_ok', {
                    'cid': consulta_id,
                    'cnpj': emp.cnpj,
                    'nome': emp.nome,
                }, namespace='/')

            # ── Finaliza ──────────────────────────────────────────────────────
            log('\n' + '=' * 50)
            log(f'CONCLUIDO — {sucessos} OK, {erros} com erros', 'ok')
            log('=' * 50)

            consulta.status      = 'concluida'
            consulta.concluida_em = datetime.utcnow()
            consulta.sucessos    = sucessos
            consulta.erros       = erros
            db.session.commit()

            socketio.emit('concluida', {
                'cid': consulta_id,
                'status': 'concluida',
                'sucessos': sucessos,
                'erros': erros,
            }, namespace='/')

        except Exception as e:
            logger.exception(f'Erro critico na consulta {consulta_id}')
            log(f'\nERRO CRITICO: {e}', 'erro')
            consulta.status = 'erro'
            db.session.commit()
            socketio.emit('concluida', {'cid': consulta_id, 'status': 'erro'}, namespace='/')


def _salvar_status(db, emp, res, mes, ano, consulta_id):
    from ..models import StatusObrigacao
    periodo = f'{mes}/{ano}'

    mapa = {
        'sitfis':       ('sitfis',       lambda r: 'ok' if r.get('sucesso') else 'erro'),
        'caixa':        ('caixa',        lambda r: 'ok' if r.get('sucesso') else 'erro'),
        'dctfweb':      ('dctfweb',      lambda r: 'ok' if r.get('transmitida') else 'pendente'),
        'mit':          ('mit',          lambda r: 'ok' if r.get('transmitida') else 'pendente'),
        'simples':      ('pgdas',        lambda r: 'nao_aplicavel' if r.get('nao_simples') else ('ok' if r.get('transmitida') else 'pendente')),
        'parcelamento': ('parcelamento', lambda r: 'erro' if r.get('tem_parcelamento') else 'ok'),
    }

    for srv_id, (srv_nome, get_st) in mapa.items():
        r = res.get(srv_id)
        if r is None:
            continue
        st  = get_st(r)
        det = str(r.get('situacao_fiscal') or r.get('situacao') or
                  r.get('status') or r.get('erro') or '')[:300]
        pdf = str(r.get('arquivo_pdf') or '').replace(
            str(db.session.get_bind()), '')

        obs = StatusObrigacao.query.filter_by(
            empresa_id=emp.id, servico=srv_nome, periodo=periodo).first()
        if not obs:
            obs = StatusObrigacao(empresa_id=emp.id, servico=srv_nome, periodo=periodo)
            db.session.add(obs)
        obs.status       = st
        obs.detalhe      = det
        obs.arquivo_nome = str(r.get('arquivo_pdf', ''))
        obs.atualizado_em = __import__('datetime').datetime.utcnow()
        obs.consulta_id  = consulta_id

    db.session.commit()


def _gerar_alertas(db, emp, res, mes, ano):
    from ..models import Alerta
    periodo = f'{mes}/{ano}'

    # Obrigações não transmitidas
    for srv_id, srv_nome in [('dctfweb','DCTFWeb'),('simples','PGDAS-D'),('mit','MIT')]:
        r = res.get(srv_id, {})
        if r.get('sucesso') is not False and not r.get('transmitida') and not r.get('nao_simples'):
            msg = f'{srv_nome} não transmitida — {periodo}'
            if not Alerta.query.filter_by(empresa_id=emp.id, mensagem=msg, lido=False).first():
                db.session.add(Alerta(empresa_id=emp.id, tipo='pendencia', mensagem=msg))

    # Débito SITFIS
    if res.get('sitfis', {}).get('possui_debito'):
        msg = 'Débito identificado no SITFIS'
        if not Alerta.query.filter_by(empresa_id=emp.id, mensagem=msg, lido=False).first():
            db.session.add(Alerta(empresa_id=emp.id, tipo='irregularidade', mensagem=msg))

    # Parcelamento ativo
    if res.get('parcelamento', {}).get('tem_parcelamento'):
        n   = res['parcelamento'].get('total', 0)
        msg = f'{n} parcelamento(s) ativo(s)'
        if not Alerta.query.filter_by(empresa_id=emp.id, mensagem=msg, lido=False).first():
            db.session.add(Alerta(empresa_id=emp.id, tipo='parcelamento', mensagem=msg))

    db.session.commit()
