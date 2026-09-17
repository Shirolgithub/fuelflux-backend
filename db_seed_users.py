import sqlite3
import bcrypt

def hash_password(password: str) -> str:
    password = password[:72].encode('utf-8')
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password, salt)
    return hashed.decode('utf-8')

def seed():
    conn = sqlite3.connect('d:\\Fuelflux\\backend\\fuelflux.db')
    cursor = conn.cursor()
    
    # Check if logistic@fuelflux.com exists
    cursor.execute("SELECT id FROM users WHERE email='logistic@fuelflux.com'")
    user = cursor.fetchone()
    
    hashed_pw = hash_password('password123')
    
    if not user:
        print("Seeding logistic@fuelflux.com...")
        cursor.execute(
            "INSERT INTO users (email, hashed_password, roles, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            ('logistic@fuelflux.com', hashed_pw, '["logistic"]', 1)
        )
        conn.commit()
        print("logistic@fuelflux.com seeded successfully.")
    else:
        print("logistic@fuelflux.com already exists. Updating password hash to password123...")
        cursor.execute(
            "UPDATE users SET hashed_password=?, roles=? WHERE email='logistic@fuelflux.com'",
            (hashed_pw, '["logistic"]')
        )
        conn.commit()
        print("Password updated successfully.")
        
    # Also ensure owner@fuelflux.com has password123
    cursor.execute("SELECT id FROM users WHERE email='owner@fuelflux.com'")
    owner = cursor.fetchone()
    if owner:
        print("Updating owner@fuelflux.com password hash...")
        cursor.execute(
            "UPDATE users SET hashed_password=? WHERE email='owner@fuelflux.com'",
            (hashed_pw,)
        )
        conn.commit()
        print("owner@fuelflux.com updated successfully.")
        
    conn.close()

if __name__ == '__main__':
    seed()
