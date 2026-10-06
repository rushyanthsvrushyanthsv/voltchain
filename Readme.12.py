from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_connection, init_database
from models import get_dashboard
from blockchain import Blockchain
import uuid

app = Flask(__name__, static_folder='.', static_url_path='')

CORS(app, resources={r"/*": {"origins": "*"}})

blockchain = Blockchain()

init_database()


# --------------------------------------------------
# HOME
# --------------------------------------------------

@app.route("/")
def home():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)


# --------------------------------------------------
# REGISTER USER
# --------------------------------------------------

@app.route("/api/register", methods=["POST"])
def register():

    data = request.json

    name = data.get("name")
    email = data.get("email")
    password = data.get("password")
    role = data.get("role")

    if not name or not email or not password or not role:
        return jsonify({
            "error": "All fields are required"
        }), 400

    if role not in ["producer", "consumer", "admin"]:
        return jsonify({
            "error": "Invalid role"
        }), 400

    hashed_password = generate_password_hash(password)

    connection = get_connection()
    cursor = connection.cursor()

    try:

        cursor.execute("""
            INSERT INTO users
            (name, email, password, role)
            VALUES (?, ?, ?, ?)
        """, (
            name,
            email,
            hashed_password,
            role
        ))

        user_id = cursor.lastrowid

        if role == "producer":

            cursor.execute("""
                INSERT INTO producers
                (user_id, energy_source)
                VALUES (?, ?)
            """, (
                user_id,
                data.get("energy_source", "Solar")
            ))

        elif role == "consumer":

            cursor.execute("""
                INSERT INTO consumers
                (user_id)
                VALUES (?)
            """, (user_id,))

        connection.commit()

        return jsonify({
            "message": "Registration successful",
            "user_id": user_id
        }), 201

    except Exception as e:

        connection.rollback()

        return jsonify({
            "error": str(e)
        }), 400

    finally:
        connection.close()


# --------------------------------------------------
# LOGIN
# --------------------------------------------------

@app.route("/api/login", methods=["POST"])
def login():

    data = request.json

    email = data.get("email")
    password = data.get("password")

    connection = get_connection()

    user = connection.execute("""
        SELECT *
        FROM users
        WHERE email = ?
    """, (email,)).fetchone()

    connection.close()

    if not user:

        return jsonify({
            "error": "User not found"
        }), 404

    if not check_password_hash(
        user["password"],
        password
    ):

        return jsonify({
            "error": "Incorrect password"
        }), 401

    return jsonify({
        "message": "Login successful",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"]
        }
    })


# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------

@app.route("/api/dashboard", methods=["GET"])
def dashboard():

    return jsonify(get_dashboard())


# --------------------------------------------------
# ADD ENERGY
# --------------------------------------------------

@app.route("/api/energy/generate", methods=["POST"])
def generate_energy():

    data = request.json

    producer_id = data.get("producer_id")
    energy = float(data.get("energy_kwh", 0))

    if energy <= 0:

        return jsonify({
            "error": "Energy must be greater than zero"
        }), 400

    connection = get_connection()
    cursor = connection.cursor()

    producer = cursor.execute("""
        SELECT *
        FROM producers
        WHERE id = ?
    """, (producer_id,)).fetchone()

    if not producer:

        connection.close()

        return jsonify({
            "error": "Producer not found"
        }), 404

    cursor.execute("""
        UPDATE producers
        SET generated_kwh = generated_kwh + ?,
            available_kwh = available_kwh + ?
        WHERE id = ?
    """, (
        energy,
        energy,
        producer_id
    ))

    cursor.execute("""
        INSERT INTO energy_readings
        (producer_id, generation_kwh)
        VALUES (?, ?)
    """, (
        producer_id,
        energy
    ))

    connection.commit()
    connection.close()

    return jsonify({
        "message": "Energy generated successfully",
        "producer_id": producer_id,
        "energy_kwh": energy
    })


# --------------------------------------------------
# ENERGY MARKET
# --------------------------------------------------

@app.route("/api/market", methods=["GET"])
def market():

    connection = get_connection()

    rows = connection.execute("""
        SELECT
            p.id,
            u.name,
            p.energy_source,
            p.available_kwh,
            p.capacity_kw
        FROM producers p
        JOIN users u
        ON p.user_id = u.id
        WHERE p.available_kwh > 0
    """).fetchall()

    connection.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


# --------------------------------------------------
# CREATE ENERGY TRANSACTION
# --------------------------------------------------

@app.route("/api/transactions", methods=["POST"])
def create_transaction():

    data = request.json

    producer_id = data.get("producer_id")
    consumer_id = data.get("consumer_id")

    energy = float(data.get("energy_kwh", 0))
    price = float(data.get("price_per_kwh", 0))

    if energy <= 0 or price <= 0:

        return jsonify({
            "error": "Energy and price must be greater than zero"
        }), 400

    connection = get_connection()
    cursor = connection.cursor()

    producer = cursor.execute("""
        SELECT *
        FROM producers
        WHERE id = ?
    """, (producer_id,)).fetchone()

    consumer = cursor.execute("""
        SELECT *
        FROM consumers
        WHERE id = ?
    """, (consumer_id,)).fetchone()

    if not producer:

        connection.close()

        return jsonify({
            "error": "Producer not found"
        }), 404

    if not consumer:

        connection.close()

        return jsonify({
            "error": "Consumer not found"
        }), 404

    if producer["available_kwh"] < energy:

        connection.close()

        return jsonify({
            "error": "Insufficient available energy"
        }), 400

    total = energy * price

    transaction_id = str(uuid.uuid4())

    # Create blockchain block
    block = blockchain.create_block(
        producer=producer_id,
        consumer=consumer_id,
        energy=energy,
        price=price,
        transaction_id=transaction_id
    )

    cursor.execute("""
        UPDATE producers
        SET available_kwh = available_kwh - ?
        WHERE id = ?
    """, (
        energy,
        producer_id
    ))

    cursor.execute("""
        UPDATE consumers
        SET consumed_kwh = consumed_kwh + ?
        WHERE id = ?
    """, (
        energy,
        consumer_id
    ))

    cursor.execute("""
        INSERT INTO transactions
        (
            producer_id,
            consumer_id,
            energy_kwh,
            price_per_kwh,
            total_amount,
            blockchain_hash,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        producer_id,
        consumer_id,
        energy,
        price,
        total,
        block["hash"],
        "COMPLETED"
    ))

    connection.commit()
    transaction_db_id = cursor.lastrowid

    connection.close()

    return jsonify({
        "message": "Energy transaction completed",
        "transaction_id": transaction_id,
        "database_transaction_id": transaction_db_id,
        "energy_kwh": energy,
        "price_per_kwh": price,
        "total_amount": total,
        "blockchain_hash": block["hash"]
    }), 201


# --------------------------------------------------
# TRANSACTION HISTORY
# --------------------------------------------------

@app.route("/api/transactions", methods=["GET"])
def transactions():

    connection = get_connection()

    rows = connection.execute("""
        SELECT *
        FROM transactions
        ORDER BY id DESC
    """).fetchall()

    connection.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


# --------------------------------------------------
# BLOCKCHAIN
# --------------------------------------------------

@app.route("/api/blockchain", methods=["GET"])
def get_blockchain():

    return jsonify({
        "chain": blockchain.chain,
        "length": len(blockchain.chain),
        "valid": blockchain.verify_chain()
    })


# --------------------------------------------------
# VERIFY BLOCKCHAIN
# --------------------------------------------------

@app.route("/api/blockchain/verify", methods=["GET"])
def verify_blockchain():

    valid = blockchain.verify_chain()

    return jsonify({
        "blockchain_valid": valid,
        "message":
            "Blockchain is valid"
            if valid
            else
            "Blockchain integrity compromised"
    })


# --------------------------------------------------
# PRODUCERS
# --------------------------------------------------

@app.route("/api/producers", methods=["GET"])
def producers():

    connection = get_connection()

    rows = connection.execute("""
        SELECT
            p.*,
            u.name,
            u.email
        FROM producers p
        JOIN users u
        ON p.user_id = u.id
    """).fetchall()

    connection.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


# --------------------------------------------------
# CONSUMERS
# --------------------------------------------------

@app.route("/api/consumers", methods=["GET"])
def consumers():

    connection = get_connection()

    rows = connection.execute("""
        SELECT
            c.*,
            u.name,
            u.email
        FROM consumers c
        JOIN users u
        ON c.user_id = u.id
    """).fetchall()

    connection.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


# --------------------------------------------------
# ENERGY READINGS
# --------------------------------------------------

@app.route("/api/readings", methods=["GET"])
def readings():

    connection = get_connection()

    rows = connection.execute("""
        SELECT *
        FROM energy_readings
        ORDER BY id DESC
    """).fetchall()

    connection.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


# --------------------------------------------------
# ADMIN STATISTICS
# --------------------------------------------------

@app.route("/api/admin/stats", methods=["GET"])
def admin_stats():

    connection = get_connection()

    users = connection.execute(
        "SELECT COUNT(*) AS count FROM users"
    ).fetchone()["count"]

    producers_count = connection.execute(
        "SELECT COUNT(*) AS count FROM producers"
    ).fetchone()["count"]

    consumers_count = connection.execute(
        "SELECT COUNT(*) AS count FROM consumers"
    ).fetchone()["count"]

    transactions_count = connection.execute(
        "SELECT COUNT(*) AS count FROM transactions"
    ).fetchone()["count"]

    total_energy = connection.execute("""
        SELECT COALESCE(SUM(energy_kwh), 0) AS total
        FROM transactions
    """).fetchone()["total"]

    revenue = connection.execute("""
        SELECT COALESCE(SUM(total_amount), 0) AS total
        FROM transactions
    """).fetchone()["total"]

    connection.close()

    return jsonify({
        "users": users,
        "producers": producers_count,
        "consumers": consumers_count,
        "transactions": transactions_count,
        "energy_traded_kwh": total_energy,
        "total_transaction_value": revenue,
        "blockchain_blocks": len(blockchain.chain),
        "blockchain_valid": blockchain.verify_chain()
    })


# --------------------------------------------------
# RUN SERVER
# --------------------------------------------------

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )