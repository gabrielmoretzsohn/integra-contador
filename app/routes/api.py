"""routes/api.py"""
import os
from flask import Blueprint, jsonify, send_file, abort, current_app
from flask_login import login_required
from datetime import datetime
from ..models import Alerta, StatusObrigacao, Empresa

bp = Blueprint('api', __name__, url_prefix='/api')

@bp.route('/alertas/nao-lidos')
@login_required
def alertas_nao_lidos():
    al = Alerta.query.filter_by(lido=False).order_by(Alerta.criado_em.desc()).limit(20).all()
    return jsonify([{
        'id': a.id, 'empresa': a.empresa.nome if a.empresa else '',
        'tipo': a.tipo, 'mensagem': a.mensagem,
        'criado': a.criado_em.strftime('%d/%m %H:%M'),
    } for a in al])

@bp.route('/status-periodo')
@login_required
def status_periodo():
    per = datetime.now().strftime('%m/%Y')
    empresas = Empresa.query.filter_by(ativa=True).all()
    resultado = []
    for emp in empresas:
        obs = {o.servico: {'status': o.status, 'detalhe': o.detalhe}
               for o in StatusObrigacao.query.filter_by(empresa_id=emp.id, periodo=per).all()}
        resultado.append({'cnpj': emp.cnpj_fmt, 'nome': emp.nome, 'obs': obs})
    return jsonify(resultado)


"""routes/arquivos.py"""
from flask import Blueprint as _BP, send_file as _sf, abort as _ab
from flask_login import login_required as _lr
import os as _os

bpa = _BP('arquivos', __name__)

@bpa.route('/relatorios/<path:nome>')
@_lr
def servir(nome):
    from flask import current_app
    pasta = current_app.config['RELATORIOS_FOLDER']
    caminho = _os.path.realpath(_os.path.join(pasta, nome))
    if not caminho.startswith(_os.path.realpath(pasta)):
        _ab(403)
    if not _os.path.exists(caminho):
        _ab(404)
    return _sf(caminho)
