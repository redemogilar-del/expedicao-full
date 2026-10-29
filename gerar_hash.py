"""Gera o valor de EXP_LOGIN_PASSWORD_HASH para colocar no Render.
Uso:  python gerar_hash.py   (digita a senha quando pedir; ela não aparece na tela)"""
import getpass
from werkzeug.security import generate_password_hash

senha = getpass.getpass('Senha nova: ')
if len(senha) < 8:
    raise SystemExit('Use pelo menos 8 caracteres.')
print('\nCopie a linha abaixo para a variável EXP_LOGIN_PASSWORD_HASH:\n')
print(generate_password_hash(senha))
