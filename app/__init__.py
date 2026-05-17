import os, json
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_mail import Mail
from flask_socketio import SocketIO

db        = SQLAlchemy()
login_mgr = LoginManager()
bcrypt    = Bcrypt()
mail      = Mail()
socketio  = SocketIO()

def create_app():
    app = Flask(__name__, template_folder='../templates', static_folder='../static')

    db_url = os.environ.get('DATABASE_URL', 'sqlite:///elo.db')
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)

    app.config.update(
        SECRET_KEY                    = os.environ.get('SECRET_KEY', 'elo-dev-secret-2024'),
        SQLALCHEMY_DATABASE_URI       = db_url,
        SQLALCHEMY_TRACK_MODIFICATIONS= False,
        MAX_CONTENT_LENGTH            = 10 * 1024 * 1024,
        UPLOAD_FOLDER                 = os.path.join(os.path.dirname(__file__), '..', 'uploads', 'certificados'),
        RELATORIOS_FOLDER             = os.path.join(os.path.dirname(__file__), '..', 'relatorios'),
        MAIL_SERVER  = os.environ.get('MAIL_SERVER', 'smtp.gmail.com'),
        MAIL_PORT    = int(os.environ.get('MAIL_PORT', 587)),
        MAIL_USE_TLS = True,
        MAIL_USERNAME= os.environ.get('MAIL_USERNAME', ''),
        MAIL_PASSWORD= os.environ.get('MAIL_PASSWORD', ''),
    )

    for pasta in [app.config['UPLOAD_FOLDER'], app.config['RELATORIOS_FOLDER']]:
        os.makedirs(pasta, exist_ok=True)

    db.init_app(app)
    bcrypt.init_app(app)
    mail.init_app(app)
    socketio.init_app(app, cors_allowed_origins='*', async_mode='eventlet',
                      logger=False, engineio_logger=False)
    login_mgr.init_app(app)
    login_mgr.login_view    = 'auth.login'
    login_mgr.login_message = 'Faca login para continuar.'

    from .models import User
    @login_mgr.user_loader
    def load_user(uid):
        return db.session.get(User, int(uid))

    app.jinja_env.filters['fromjson'] = lambda s: (json.loads(s) if s else [])
    app.jinja_env.filters['basename'] = lambda s: os.path.basename(s or '')

    from .routes.auth          import bp as auth_bp
    from .routes.dashboard     import bp as dash_bp
    from .routes.empresas      import bp as emp_bp
    from .routes.diagnosticos  import bp as diag_bp
    from .routes.caixa_postal  import bp as cx_bp
    from .routes.guias         import bp as gui_bp
    from .routes.usuarios      import bp as usr_bp
    from .routes.configuracoes import bp as cfg_bp
    from .routes.api           import bp as api_bp, bpa as arq_bp

    for bp_ in [auth_bp, dash_bp, emp_bp, diag_bp, cx_bp, gui_bp,
                usr_bp, cfg_bp, api_bp, arq_bp]:
        app.register_blueprint(bp_)

    with app.app_context():
        db.create_all()
        _seed()

    return app

def _seed():
    from .models import User
    if not User.query.filter_by(email='admin@elogestao.com.br').first():
        u = User(
            nome='Administrador',
            email='admin@elogestao.com.br',
            senha_hash=bcrypt.generate_password_hash('elo@2024').decode(),
            perfil='admin', ativo=True,
        )
        db.session.add(u)
        db.session.commit()
