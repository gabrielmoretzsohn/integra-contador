"""routes/empresas.py"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from ..models import Empresa, StatusObrigacao, db
from datetime import datetime

bp = Blueprint('empresas', __name__, url_prefix='/empresas')

@bp.route('/')
@login_required
def index():
    per = request.args.get('periodo', datetime.now().strftime('%m/%Y'))
    empresas = Empresa.query.filter_by(ativa=True).order_by(Empresa.nome).all()

    # Status de cada empresa para o período
    status_map = {}
    for emp in empresas:
        obs = {o.servico: o for o in
               StatusObrigacao.query.filter_by(empresa_id=emp.id, periodo=per).all()}
        status_map[emp.id] = obs

    return render_template('empresas/index.html',
        empresas=empresas, status_map=status_map, periodo=per)

@bp.route('/nova', methods=['POST'])
@login_required
def nova():
    cnpj = request.form['cnpj'].replace('.','').replace('/','').replace('-','')
    nome = request.form['nome'].strip()
    regime = request.form.get('regime','lucro')
    if not Empresa.query.filter_by(cnpj=cnpj).first():
        db.session.add(Empresa(cnpj=cnpj, nome=nome, regime=regime))
        db.session.commit()
        flash(f'{nome} cadastrada com sucesso.', 'success')
    else:
        flash('CNPJ já cadastrado.', 'danger')
    return redirect(url_for('empresas.index'))

@bp.route('/importar', methods=['POST'])
@login_required
def importar():
    n = 0
    for linha in request.form.get('lista','').splitlines():
        p = linha.strip().split(None, 2)
        if not p: continue
        cnpj = p[0].replace('.','').replace('/','').replace('-','')
        if not cnpj.isdigit() or len(cnpj) not in (11,14): continue
        nome = p[1].strip() if len(p)>1 else cnpj
        regime = p[2].strip().lower() if len(p)>2 else 'lucro'
        if regime not in ('simples','lucro','mei'): regime = 'lucro'
        if not Empresa.query.filter_by(cnpj=cnpj).first():
            db.session.add(Empresa(cnpj=cnpj, nome=nome, regime=regime))
            n += 1
    db.session.commit()
    flash(f'{n} empresa(s) importada(s).', 'success')
    return redirect(url_for('empresas.index'))

@bp.route('/<int:eid>/toggle')
@login_required
def toggle(eid):
    e = Empresa.query.get_or_404(eid)
    e.ativa = not e.ativa
    db.session.commit()
    return jsonify({'ativa': e.ativa})
