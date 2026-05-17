"""routes/configuracoes.py"""
import os
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from ..models import Config, db

bp = Blueprint('configuracoes', __name__, url_prefix='/configuracoes')

def gestor_req(f):
    @wraps(f)
    def d(*a,**k):
        if not current_user.is_gestor:
            flash('Acesso restrito.','danger')
            return redirect(url_for('dashboard.index'))
        return f(*a,**k)
    return d

@bp.route('/', methods=['GET','POST'])
@login_required
@gestor_req
def index():
    if request.method == 'POST':
        for k in ['consumer_key','consumer_secret','cert_senha',
                  'cnpj_escritorio','nome_escritorio']:
            v = request.form.get(k,'').strip()
            if v: Config.set(k, v)

        cert = request.files.get('cert_file')
        if cert and cert.filename:
            fname = secure_filename(cert.filename)
            dest  = os.path.join(current_app.config['UPLOAD_FOLDER'], fname)
            cert.save(dest)
            Config.set('cert_path', dest)
            flash(f'Certificado "{fname}" enviado com sucesso!', 'success')

        flash('Configurações salvas.', 'success')
        return redirect(url_for('configuracoes.index'))

    cfg = {c.chave: c.valor for c in Config.query.all()}

    # Verifica status da API
    api_ok = bool(Config.get('consumer_key') and Config.get('cert_path'))

    return render_template('configuracoes/index.html', cfg=cfg, api_ok=api_ok)
