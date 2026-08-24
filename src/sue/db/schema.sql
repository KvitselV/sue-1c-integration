-- Схема СУЭ (экспорт логической модели; фактическое создание — SQLAlchemy create_all / PostgreSQL)

CREATE TABLE stores (
  id INTEGER PRIMARY KEY,
  source_ref VARCHAR(64) NOT NULL UNIQUE,
  code VARCHAR(64) NOT NULL,
  name VARCHAR(255) NOT NULL,
  city VARCHAR(128),
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  loaded_at TIMESTAMP NOT NULL
);

CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  source_ref VARCHAR(64) NOT NULL UNIQUE,
  sku VARCHAR(64) NOT NULL,
  name VARCHAR(255) NOT NULL,
  category VARCHAR(128) NOT NULL,
  loaded_at TIMESTAMP NOT NULL
);

CREATE TABLE sale_documents (
  id INTEGER PRIMARY KEY,
  source_ref VARCHAR(64) NOT NULL UNIQUE,
  store_id INTEGER NOT NULL REFERENCES stores(id),
  doc_date DATE NOT NULL,
  doc_number VARCHAR(64) NOT NULL,
  loaded_at TIMESTAMP NOT NULL
);

CREATE TABLE sale_lines (
  id INTEGER PRIMARY KEY,
  source_ref VARCHAR(64) NOT NULL UNIQUE,
  document_id INTEGER NOT NULL REFERENCES sale_documents(id),
  store_id INTEGER NOT NULL REFERENCES stores(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  sale_date DATE NOT NULL,
  quantity DOUBLE PRECISION NOT NULL,
  revenue DOUBLE PRECISION NOT NULL,
  cost_accounting DOUBLE PRECISION NULL, -- NULL => modeled при расчёте KPI
  loaded_at TIMESTAMP NOT NULL
);

CREATE TABLE etl_runs (
  id INTEGER PRIMARY KEY,
  source_system VARCHAR(64) NOT NULL,
  source_file VARCHAR(512) NOT NULL,
  status VARCHAR(32) NOT NULL,
  started_at TIMESTAMP NOT NULL,
  finished_at TIMESTAMP NULL,
  records_accepted INTEGER NOT NULL DEFAULT 0,
  records_rejected INTEGER NOT NULL DEFAULT 0,
  message TEXT NULL
);

CREATE TABLE etl_errors (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES etl_runs(id),
  stage VARCHAR(64) NOT NULL,
  entity VARCHAR(64) NULL,
  source_ref VARCHAR(64) NULL,
  detail TEXT NOT NULL
);

CREATE TABLE model_params (
  id INTEGER PRIMARY KEY,
  key VARCHAR(128) NOT NULL UNIQUE,
  value_json TEXT NOT NULL,
  description TEXT NOT NULL,
  updated_at TIMESTAMP NOT NULL
);