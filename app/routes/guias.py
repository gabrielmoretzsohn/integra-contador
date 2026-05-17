"""routes/guias.py"""
from flask import Blueprint, render_template
from flask_login import login_required
from ..models import Empresa, StatusObrigacao
from datetime import datetime

bp = Blueprint('guias', __name__, url_prefix='/guias')

@bp.route('/')
@login_required
def index():
    per = datetime.now().strftime('%m/%Y')
    empresas = Empresa.query.filter_by(ativa=True).order_by(Empresa.nome).all()
    dados = []
    for emp in empresas:
        obs = {o.servico: o for o in StatusObrigacao.query.filter_by(
            empresa_id=emp.id, periodo=per).all()}
        dados.append({'emp': emp, 'obs': obs})
    return render_template('guias/index.html', dados=dados, periodo=per)
