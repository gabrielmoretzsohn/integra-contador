"""routes/caixa_postal.py"""
from flask import Blueprint, render_template, request
from flask_login import login_required
from ..models import Empresa, StatusObrigacao
from datetime import datetime

bp = Blueprint('caixa_postal', __name__, url_prefix='/caixa-postal')

@bp.route('/')
@login_required
def index():
    per = datetime.now().strftime('%m/%Y')
    empresas = Empresa.query.filter_by(ativa=True).order_by(Empresa.nome).all()
    dados = []
    for emp in empresas:
        obs = StatusObrigacao.query.filter_by(
            empresa_id=emp.id, servico='caixa', periodo=per).first()
        dados.append({'emp': emp, 'obs': obs})
    return render_template('caixa_postal/index.html', dados=dados, periodo=per)
