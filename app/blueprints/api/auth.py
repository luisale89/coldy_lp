import uuid, re

from flask import Blueprint, url_for, jsonify, request, current_app
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash
from flask_jwt_extended import (
    create_access_token, create_refresh_token, jwt_required, get_jwt_identity
)

from ...models import (
    User, TokenBlacklist
)

from ...extensions import jwt, db

from ...utils.exceptions import (
    APIException, TokenNotFound
)

from ...utils.helpers import (
    normalize_names, is_token_revoked, add_token_to_database, get_user_tokens, 
    revoke_token, unrevoke_token, prune_database, revoke_all_tokens
)

auth = Blueprint('auth', __name__, url_prefix='/api/auth')

@auth.errorhandler(APIException)
def handle_invalid_usage(error):
    return jsonify(error.to_dict()), error.status_code

@jwt.token_in_blacklist_loader
def check_if_token_revoked(decoded_token):
    return is_token_revoked(decoded_token)

@auth.route('/sign-up', methods=['POST']) #normal signup
def sign_up():
    
    """
    * PUBLIC ENDPOINT *
    Crear un nuevo usuario para la aplicación.
    requerido: {
        "email": email,
        "password": psw,
        "fname": fname,
        "lname": lname
    }
    respuesta: {
        "success":"created", 201
    }
    """
    #Regular expression that checks a valid email
    ereg = '^\w+([\.-]?\w+)*@\w+([\.-]?\w+)*(\.\w{2,3})+$'
    #Regular expression that checks a secure password
    preg = '^.*(?=.{8,})(?=.*\d)(?=.*[a-z])(?=.*[A-Z]).*$'

    if not request.is_json:
        raise APIException("json request only")
    
    body = request.get_json(silent=True)
    if body is None:
        raise APIException("not body in request")

    if 'email' not in body:
        raise APIException("email not found in request")
    elif not re.search(ereg, body['email']):
        raise APIException("invalid email format")

    if 'password' not in body:
        raise APIException("password not found in request")
    elif not re.search(preg, body['password']):
        raise APIException("insecure password")

    if 'fname' not in body:
        raise APIException("fname not found in request")
    fname = normalize_names(body['fname'])

    if 'lname' not in body:
        raise APIException("lname not found in request")
    lname = normalize_names(body['lname'])

    try:
        new_user = User(
            email=body['email'], 
            password=body['password'], 
            fname=fname,
            lname=lname, 
            public_id=str(uuid.uuid4())
        )
        db.session.add(new_user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise APIException("user already created - login instead") # la columna email es unica,por eso este error significa solamente que el email ya existe

    return jsonify({'success': 'created'}), 201


@auth.route('/login', methods=['POST']) #normal login
def login():
    """
    PUBLIC ENDPOINT
    requerido: {
        "email": email,
        "password": password
    }
    respuesta: {
        "access_token": jwt_access_token,
        "user": {
            "id": id
            "fname": fname,
            "lname": lname,
            "user_picture": url_of_pic
        }
    }
    """
    if not request.is_json:
        raise APIException("not json request")

    body = request.get_json(silent=True)
    if body is None:
        raise APIException("not json request")

    if 'email' not in body:
        raise APIException("misising email")

    if 'password' not in body:
        raise APIException("missing password")

    user = User.query.filter_by(email=body['email']).first()
    if user is None:
        raise APIException("user not found", status_code=404)

    if user.password_hash is None:
        raise APIException("user logged with social login")

    if not check_password_hash(user.password_hash, body['password']):
        raise APIException("wrong password", status_code=404)
    
    access_token = create_access_token(identity=user.public_id)
    add_token_to_database(access_token, current_app.config['JWT_IDENTITY_CLAIM'])

    return jsonify({"user": user.serialize_public(), "access_token": access_token})


@auth.route("/token", methods=["GET"])
@jwt_required
def get_tokens():
    user_identity = get_jwt_identity()
    all_tokens = get_user_tokens(user_identity)
    ret = [token.serialize() for token in all_tokens]
    return jsonify(ret), 200


@auth.route('/token/<token_id>', methods=['PUT'])
@jwt_required
def modify_token(token_id):
    # Get and verify the desired revoked status from the body
    json_data = request.get_json(silent=True)
    if not json_data:
        return jsonify({"error": "Missing 'revoke' in body"}), 400
    revoke = json_data.get('revoke', None)
    if revoke is None:
        return jsonify({"error": "Missing 'revoke' in body"}), 400
    if not isinstance(revoke, bool):
        return jsonify({"error": "'revoke' must be a boolean"}), 400

    # Revoke or unrevoke the token based on what was passed to this function
    user_identity = get_jwt_identity()
    try:
        if revoke:
            revoke_token(token_id, user_identity)
            return jsonify({'msg': 'Token revoked'}), 200
        else:
            unrevoke_token(token_id, user_identity)
            return jsonify({'msg': 'Token unrevoked'}), 200
    except TokenNotFound:
        return jsonify({'msg': 'The specified token was not found'}), 404


@auth.route('/logout', methods=['GET']) #logout everywhere
@jwt_required
def logout_user():
    if not request.is_json:
        raise APIException("JSON request only")

    user_identity = get_jwt_identity()
    tokens = TokenBlacklist.query.filter_by(user_identity=user_identity, revoked=False).all()
    for token in tokens:
        token.revoked = True

    db.session.commit()
    return jsonify({"success": "user logged out"}), 200


@auth.route('/prune-db', methods=['GET']) #This must be a admin only endpoint.
@jwt_required
def prune_db():
    prune_database()
    return jsonify({"success": "db pruned correctly"}), 200