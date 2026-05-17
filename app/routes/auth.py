"""routes/auth.py"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime
from .. import db, bcrypt
from ..models import User

bp = Blueprint('auth', __name__)

@bp.route('/', methods=['GET','POST'])
@bp.route('/login', methods=['GET','POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
    if request.method == 'POST':
        u = User.query.filter_by(email=request.form['email'].strip().lower()).first()
        if u and u.ativo and bcrypt.check_password_hash(u.senha_hash, request.form['senha']):
            login_user(u, remember=True)
            u.ultimo_acesso = datetime.utcnow()
            db.session.commit()
            return redirect(url_for('dashboard.index'))
        flash('E-mail ou senha incorretos.', 'danger')
    return render_template('auth/login.html')

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

@bp.route('/perfil', methods=['GET','POST'])
@login_required
def perfil():
    if request.method == 'POST':
        current_user.nome = request.form.get('nome', current_user.nome)
        ns = request.form.get('nova_senha','')
        sa = request.form.get('senha_atual','')
        if ns and sa and bcrypt.check_password_hash(current_user.senha_hash, sa):
            current_user.senha_hash = bcrypt.generate_password_hash(ns).decode()
            flash('Senha alterada!', 'success')
        db.session.commit()
    return render_template('auth/perfil.html')
