from datetime import datetime
from flask_login import UserMixin
from . import db


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    nome          = db.Column(db.String(100), nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    senha_hash    = db.Column(db.String(200), nullable=False)
    perfil        = db.Column(db.String(20), default='operador')  # admin|gestor|operador
    ativo         = db.Column(db.Boolean, default=True)
    criado_em     = db.Column(db.DateTime, default=datetime.utcnow)
    ultimo_acesso = db.Column(db.DateTime)

    @property
    def is_admin(self):  return self.perfil == 'admin'
    @property
    def is_gestor(self): return self.perfil in ('admin', 'gestor')


class Config(db.Model):
    __tablename__ = 'configs'
    id    = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(60), unique=True, nullable=False)
    valor = db.Column(db.Text, default='')

    @staticmethod
    def get(chave, padrao=''):
        r = Config.query.filter_by(chave=chave).first()
        return r.valor if r else padrao

    @staticmethod
    def set(chave, valor):
        r = Config.query.filter_by(chave=chave).first()
        if r:
            r.valor = str(valor)
        else:
            db.session.add(Config(chave=chave, valor=str(valor)))
        db.session.commit()


class Empresa(db.Model):
    __tablename__ = 'empresas'
    id        = db.Column(db.Integer, primary_key=True)
    cnpj      = db.Column(db.String(14), unique=True, nullable=False)
    nome      = db.Column(db.String(150), nullable=False)
    regime    = db.Column(db.String(20), default='lucro')
    ativa     = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    status    = db.relationship('StatusObrigacao', backref='empresa',
                                 lazy=True, cascade='all,delete-orphan')
    alertas   = db.relationship('Alerta', backref='empresa',
                                 lazy=True, cascade='all,delete-orphan')

    @property
    def cnpj_fmt(self):
        c = self.cnpj.zfill(14)
        return f'{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}'


class Consulta(db.Model):
    __tablename__ = 'consultas'
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'))
    servicos     = db.Column(db.String(300))
    n_empresas   = db.Column(db.Integer, default=0)
    status       = db.Column(db.String(20), default='pendente')
    iniciada_em  = db.Column(db.DateTime, default=datetime.utcnow)
    concluida_em = db.Column(db.DateTime)
    log          = db.Column(db.Text, default='')
    sucessos     = db.Column(db.Integer, default=0)
    erros        = db.Column(db.Integer, default=0)
    usuario      = db.relationship('User', backref='consultas')


class StatusObrigacao(db.Model):
    __tablename__ = 'status_obrigacoes'
    id            = db.Column(db.Integer, primary_key=True)
    empresa_id    = db.Column(db.Integer, db.ForeignKey('empresas.id'))
    servico       = db.Column(db.String(50))
    periodo       = db.Column(db.String(10))
    status        = db.Column(db.String(20))   # ok|pendente|erro|nao_aplicavel
    detalhe       = db.Column(db.String(400))
    arquivo_nome  = db.Column(db.String(200))  # nome do arquivo PDF gerado
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow)
    consulta_id   = db.Column(db.Integer, db.ForeignKey('consultas.id'))


class Alerta(db.Model):
    __tablename__ = 'alertas'
    id         = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'))
    tipo       = db.Column(db.String(50))
    mensagem   = db.Column(db.String(300))
    lido       = db.Column(db.Boolean, default=False)
    criado_em  = db.Column(db.DateTime, default=datetime.utcnow)
