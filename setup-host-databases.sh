#!/bin/bash
# Setup script for PostgreSQL and Redis on host system for invisible-crawler

set -e

echo "========================================="
echo "Installing PostgreSQL and Redis on host"
echo "========================================="

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

# Update package list
echo -e "${YELLOW}Updating package list...${NC}"
apt update

# Install PostgreSQL
echo -e "${YELLOW}Installing PostgreSQL...${NC}"
apt install -y postgresql postgresql-contrib

# Install Redis
echo -e "${YELLOW}Installing Redis...${NC}"
apt install -y redis-server

# Start and enable services
echo -e "${YELLOW}Starting services...${NC}"
systemctl enable postgresql
systemctl start postgresql
systemctl enable redis-server
systemctl start redis-server

# Get the database password from .env.prod or ask for it
if [ -f ".env.prod" ]; then
    DB_PASSWORD=$(grep "^POSTGRES_PASSWORD=" .env.prod | cut -d'=' -f2)
    if [ -z "$DB_PASSWORD" ] || [ "$DB_PASSWORD" = "ChangeMeSecurePassword123" ]; then
        read -sp "Enter password for PostgreSQL 'invisible' user: " DB_PASSWORD
        echo
    fi
else
    read -sp "Enter password for PostgreSQL 'invisible' user: " DB_PASSWORD
    echo
fi

# Create PostgreSQL database and user
echo -e "${YELLOW}Creating PostgreSQL database and user...${NC}"
sudo -u postgres psql <<EOF
-- Create user if not exists
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_user WHERE usename = 'invisible') THEN
        CREATE USER invisible WITH PASSWORD '$DB_PASSWORD';
    END IF;
END
\$\$;

-- Create database if not exists
SELECT 'CREATE DATABASE invisible'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'invisible')\gexec

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE invisible TO invisible;

-- Connect to invisible database and grant schema privileges
\c invisible
GRANT ALL ON SCHEMA public TO invisible;

EOF

# Configure Redis to allow connections (already allows localhost by default)
echo -e "${YELLOW}Verifying Redis configuration...${NC}"

# Check PostgreSQL is listening on localhost
echo -e "${YELLOW}Verifying PostgreSQL is listening...${NC}"
sudo -u postgres psql -c "SELECT version();" > /dev/null 2>&1 && echo -e "${GREEN}✓ PostgreSQL is running${NC}" || echo "✗ PostgreSQL check failed"

# Check Redis is listening
redis-cli ping > /dev/null 2>&1 && echo -e "${GREEN}✓ Redis is running${NC}" || echo "✗ Redis check failed"

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}Setup Complete!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "PostgreSQL Details:"
echo "  Host: localhost (or host.docker.internal from containers)"
echo "  Port: 5432"
echo "  Database: invisible"
echo "  User: invisible"
echo ""
echo "Redis Details:"
echo "  Host: localhost (or host.docker.internal from containers)"
echo "  Port: 6379"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Update .env.prod with the correct password"
echo "2. Build and start the crawler:"
echo "   cd /opt/invisible-crawler"
echo "   docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml build"
echo "   docker compose --env-file .env.prod -f docker-compose.yml run --rm --profile ops migrate"
echo "   docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d crawler"
echo ""
