"""routes/diagnosticos.py — Consultas fiscais (SITFIS, DCTFWeb, MIT, PGDAS, Parcelamentos)"""
import json, threading
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, current_app
from flask_login import login_required, current_user
from datetime import datetime
from ..models import Empresa, Consulta, Config, StatusObrigacao, db
from .. import socketio

bp = Blueprint('diagnosticos', __name__, url_prefix='/diagnosticos')

SERVICOS = [
    {'id':'sitfis',         'nome':'Diagnóstico Fiscal (SITFIS)',    'icone':'fa-chart-bar',      'grupo':'RFB'},
    {'id':'caixa',          'nome':'Caixa Postal RFB',               'icone':'fa-envelope',       'grupo':'RFB'},
    {'id':'caixa_nao_lidas','nome':'Caixa Postal — PDF não lidas',   'icone':'fa-envelope-open',  'grupo':'RFB'},
    {'id':'dctfweb',        'nome':'DCTFWeb',                        'icone':'fa-file-invoice',   'grupo':'Declarações'},
    {'id':'mit',            'nome':'MIT — Módulo de Inclusão',       'icone':'fa-file-alt',       'grupo':'Declarações'},
    {'id':'simples',        'nome':'PGDAS-D (Simples Nacional)',     'icone':'fa-store',          'grupo':'Declarações'},
    {'id':'parcelamento',   'nome':'Parcelamentos Ativos',           'icone':'fa-hand-holding-usd','grupo':'Débitos'},
]

@bp.route('/')
@login_required
def index():
    per = datetime.now().strftime('%m/%Y')
    empresas = Empresa.query.filter_by(ativa=True).order_by(Empresa.nome).all()
    consultas = Consulta.query.order_by(Consulta.iniciada_em.desc()).limit(10).all()

    # Status atual por empresa
    status_map = {}
    for emp in empresas:
        obs = {o.servico: o for o in
               StatusObrigacao.query.filter_by(empresa_id=emp.id, periodo=per).all()}
        status_map[emp.id] = obs

    return render_template('diagnosticos/index.html',
        empresas=empresas, servicos=SERVICOS,
        consultas=consultas, status_map=status_map,
        periodo=per,
        mes=datetime.now().strftime('%m'),
        ano=datetime.now().strftime('%Y'),
    )


@bp.route('/iniciar', methods=['POST'])
@login_required
def iniciar():
    data     = request.json or {}
    servicos = data.get('servicos', [])
    cnpjs    = data.get('empresas', [])
    mes      = data.get('mes', datetime.now().strftime('%m'))
    ano      = data.get('ano', datetime.now().strftime('%Y'))

    if not Config.get('cert_path'):
        return jsonify({'erro': 'Certificado não configurado. Acesse Configurações.'}), 400
    if not servicos or not cnpjs:
        return jsonify({'erro': 'Selecione serviços e empresas.'}), 400

    consulta = Consulta(
        user_id    = current_user.id,
        servicos   = json.dumps(servicos),
        n_empresas = len(cnpjs),
        status     = 'pendente',
    )
    db.session.add(consulta)
    db.session.commit()

    cfg = {k: Config.get(k) for k in
           ['consumer_key','consumer_secret','cert_path','cert_senha','cnpj_escritorio']}
    app = current_app._get_current_object()

    threading.Thread(
        target=_run_engine,
        args=(app, consulta.id, cfg, servicos, cnpjs, {'mes': mes, 'ano': ano}),
        daemon=True,
    ).start()

    return jsonify({'consulta_id': consulta.id})


@bp.route('/sincronizar')
@login_required
def sincronizar():
    """Sincronização rápida: SITFIS + Caixa Postal para todas as empresas."""
    if not Config.get('cert_path'):
        from flask import flash
        flash('Configure o certificado digital primeiro.', 'danger')
        return redirect(url_for('configuracoes.index'))

    cnpjs = [e.cnpj for e in Empresa.query.filter_by(ativa=True).all()]
    if not cnpjs:
        from flask import flash
        flash('Nenhuma empresa cadastrada.', 'danger')
        return redirect(url_for('dashboard.index'))

    consulta = Consulta(
        user_id    = current_user.id,
        servicos   = json.dumps(['sitfis','caixa','dctfweb']),
        n_empresas = len(cnpjs),
        status     = 'pendente',
    )
    db.session.add(consulta)
    db.session.commit()

    cfg = {k: Config.get(k) for k in
           ['consumer_key','consumer_secret','cert_path','cert_senha','cnpj_escritorio']}
    app = current_app._get_current_object()
    mes = datetime.now().strftime('%m')
    ano = datetime.now().strftime('%Y')

    threading.Thread(
        target=_run_engine,
        args=(app, consulta.id, cfg, ['sitfis','caixa','dctfweb'], cnpjs,
              {'mes': mes, 'ano': ano}),
        daemon=True,
    ).start()

    return redirect(url_for('diagnosticos.progresso', cid=consulta.id))


@bp.route('/progresso/<int:cid>')
@login_required
def progresso(cid):
    c = Consulta.query.get_or_404(cid)
    return render_template('diagnosticos/progresso.html', consulta=c)


@bp.route('/status/<int:cid>')
@login_required
def status(cid):
    c = Consulta.query.get_or_404(cid)
    return jsonify({
        'status':   c.status,
        'log':      (c.log or '')[-5000:],
        'sucessos': c.sucessos,
        'erros':    c.erros,
    })


def _run_engine(app, consulta_id, cfg, servicos, cnpjs, periodo):
    from ..engine import executar_consulta
    executar_consulta(app, consulta_id, cfg, servicos, cnpjs, periodo)
