"""routes/usuarios.py"""
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from ..models import User, db
from .. import bcrypt

bp = Blueprint('usuarios', __name__, url_prefix='/usuarios')

def gestor_req(f):
    @wraps(f)
    def d(*a,**k):
        if not current_user.is_gestor:
            flash('Acesso restrito.','danger')
            return redirect(url_for('dashboard.index'))
        return f(*a,**k)
    return d

@bp.route('/')
@login_required
@gestor_req
def index():
    return render_template('usuarios/index.html',
        users=User.query.order_by(User.nome).all())

@bp.route('/novo', methods=['GET','POST'])
@login_required
@gestor_req
def novo():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        if User.query.filter_by(email=email).first():
            flash('E-mail já cadastrado.','danger')
        else:
            db.session.add(User(
                nome=request.form['nome'].strip(), email=email,
                senha_hash=bcrypt.generate_password_hash(request.form['senha']).decode(),
                perfil=request.form['perfil'], ativo=True,
            ))
            db.session.commit()
            flash('Usuário criado!','success')
            return redirect(url_for('usuarios.index'))
    return render_template('usuarios/form.html', u=None)

@bp.route('/<int:uid>/editar', methods=['GET','POST'])
@login_required
@gestor_req
def editar(uid):
    u = User.query.get_or_404(uid)
    if request.method == 'POST':
        u.nome   = request.form['nome'].strip()
        u.perfil = request.form['perfil']
        u.ativo  = request.form.get('ativo') == 'on'
        ns = request.form.get('nova_senha','').strip()
        if ns: u.senha_hash = bcrypt.generate_password_hash(ns).decode()
        db.session.commit()
        flash('Usuário atualizado.','success')
        return redirect(url_for('usuarios.index'))
    return render_template('usuarios/form.html', u=u)
