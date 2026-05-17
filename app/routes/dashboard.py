"""routes/dashboard.py"""
import json
from flask import Blueprint, render_template, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timedelta
from ..models import Empresa, StatusObrigacao, Alerta, Consulta, db
from sqlalchemy import func

bp = Blueprint('dashboard', __name__)

@bp.route('/dashboard')
@login_required
def index():
    per = datetime.now().strftime('%m/%Y')
    total_empresas = Empresa.query.filter_by(ativa=True).count()

    # CNDs válidas = empresas com SITFIS ok
    cnds_ok = StatusObrigacao.query.filter_by(periodo=per, servico='sitfis', status='ok').count()

    # Alertas pendentes
    alertas_pendentes = Alerta.query.filter_by(lido=False).count()

    # Mensagens não lidas (soma do total na caixa postal)
    msgs_row = StatusObrigacao.query.filter_by(servico='caixa').first()
    msgs_nao_lidas = 0

    # Guias pendentes (DCTF + PGDAS + MIT não transmitidos)
    guias_pendentes = StatusObrigacao.query.filter(
        StatusObrigacao.periodo == per,
        StatusObrigacao.servico.in_(['dctfweb','pgdas','mit']),
        StatusObrigacao.status == 'pendente'
    ).count()

    # Próxima sincronização
    ultima = Consulta.query.order_by(Consulta.concluida_em.desc()).first()
    if ultima and ultima.concluida_em:
        proxima = ultima.concluida_em + timedelta(days=15)
        dias = (proxima - datetime.utcnow()).days
        if dias > 0:
            proxima_sync = f"Em {dias} dia{'s' if dias>1 else ''}"
        else:
            proxima_sync = "Hoje"
    else:
        proxima_sync = "Não agendada"

    # Taxa de regularidade
    if total_empresas > 0:
        regulares = db.session.query(func.count(func.distinct(StatusObrigacao.empresa_id)))\
            .filter(StatusObrigacao.periodo == per, StatusObrigacao.status == 'ok').scalar() or 0
        taxa = round(regulares / total_empresas * 100)
    else:
        taxa = 0

    # Últimas empresas
    ultimas_empresas = Empresa.query.filter_by(ativa=True)\
        .order_by(Empresa.criado_em.desc()).limit(5).all()

    # Status de cada empresa
    status_emp = {}
    for obs in StatusObrigacao.query.filter_by(periodo=per).all():
        emp = Empresa.query.get(obs.empresa_id)
        if emp:
            atual = status_emp.get(emp.cnpj, 'ok')
            if obs.status == 'pendente' and atual != 'erro':
                status_emp[emp.cnpj] = 'pendente'
            elif obs.status == 'erro':
                status_emp[emp.cnpj] = 'erro'
            elif obs.status == 'ok' and atual == 'ok':
                status_emp[emp.cnpj] = 'ok'

    # Alertas recentes
    alertas = Alerta.query.filter_by(lido=False)\
        .order_by(Alerta.criado_em.desc()).limit(6).all()

    return render_template('dashboard/index.html',
        total_empresas=total_empresas,
        cnds_ok=cnds_ok,
        alertas_pendentes=alertas_pendentes,
        msgs_nao_lidas=msgs_nao_lidas,
        guias_pendentes=guias_pendentes,
        proxima_sync=proxima_sync,
        taxa_regularidade=taxa,
        ultimas_empresas=ultimas_empresas,
        status_emp=status_emp,
        alertas=alertas,
    )


@bp.route('/alertas')
@login_required
def alertas():
    al = Alerta.query.order_by(Alerta.criado_em.desc()).limit(50).all()
    return render_template('dashboard/alertas.html', alertas=al)


@bp.route('/alertas/<int:aid>/ler')
@login_required
def marcar_lido(aid):
    a = Alerta.query.get_or_404(aid)
    a.lido = True
    db.session.commit()
    return jsonify({'ok': True})
