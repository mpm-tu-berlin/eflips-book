# Installing the Software Stack

---

A video guide for setting the software up on a freshly Ubuntu 24.04 system is available [here](<media/eflips-depot video tutorial.mp4>).

---

The eflips software stack is based on the [Python](https://www.python.org/downloads/) programming language, a [PostgeSQL](https://www.postgresql.org/) database server with the [PostGIS]([https://postgis.net/]) extension and the (optional) [Poetry](https://python-poetry.org/) dependency manager. The eflips software stack is free Software, with the dependencies being free and/or open-source software. All dependencies are available on most major operating systems, however due to the difficulty of installing PostGIS on Windows, only macOS and Linux operating systems are officially supported.

## Database

This section shows how to install the PostgreSQL+PostGIS database on 

### macOS

1. Install [Homebrew](https://brew.sh/) if it's not already installed on your system.
2. Install [PostGIS](https://formulae.brew.sh/formula/postgis) using homebrew. PostgreSQL is automatically installed as a dependency.
3. You may have to run `brew services start postgresql` in order to start the database server.

### Linux (Debian-like)

PostGIS can be be installed using `sudo apt install postgis`.

### Common

The software requires a user and a database with the `postgis`, `postgis_raster` and `btree_gist` extensions. In a PostgreSQL shell (obtained using `sudo -u postgres psql` on Linux or `psql` on macOS) this can be created using the following commands:

```sql
CREATE USER eflips WITH ENCRYPTED PASSWORD 'YOUR_PASSWORD_HERE';
CREATE DATABASE eflips OWNER eflips;
CREATE EXTENSION postgis;
CREATE EXTENSION postgis_raster;
CREATE EXTENSION btree_gist;
```

## Python packages

It is recommended to use the the [Poetry](https://python-poetry.org/) dependency manager in order to install the required packages. A `pyproject.toml` cotaining all the packages of the eflips software stack is:

```toml
[tool.poetry]
name = "eflips-book"
version = "0.1.0"
description = "Sample Scripts for the eFLIPS book"
authors = ["Ludger Heide <ludger.heide@tu-berlin.de>"]
license = "AGPLv3"
readme = "README.md"
package-mode = false

[tool.poetry.dependencies]
python = "^3.12"
eflips-depot = "^3.2.5"
eflips-ingest = "^1.2.3"
eflips-eval = "^1.2.1"


[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

If poetry is not used, the following `requirements.txt` contains the packages (for use with `pip`)

```
eflips-depot
eflips-eval
eflips-ingest
```

## Environment variables

When writing custom Python scripts, it is recommended to not store the database credentials in the script itself for security and cross-device-compatibility reasons. Rather, they should be read from an environment variable. The following Python syntax can be used:

```python
if "DATABASE_URL" not in os.environ:
	raise ValueError(
		"The database url must be specified in the environment variable DATABASE_URL."
	)
DATABASE_URL = os.environ["DATABASE_URL"]
```

In the unix shells, `export DATABASE_URL=postgresql://eflips:YOUR_PASSWORD_HERE@localhost/eflips` can then be used to set the database URL for this shell. 