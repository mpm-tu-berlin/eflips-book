# Importing VDV Schedules

The [VDV 451](https://www.vdv.de/vdv-schrift-451.pdfx) standard is a widely used German standard for rotation data. A set of VDV-files (with the .x10) file extension can contain all network information, including rotations and vehicle type assignment. However, it seems that this standard is not always followed fully and that many semi-compatible implementations exist. As such, you should be careful when importing a new dataset.

## Input data format

The data should be stored in a `.zip` file, with the individual `.x10` files on the *top level* directory of the zip file.

## Importing using the API

The [eflips-ingest](https://github.com/mpm-tu-berlin/eflips-ingest) package includes an API for ingesters, which is specified in `eflips/ingest/base.py`. It consists of two steps, a (quick) `prepare()` method that roughly checks the data and saves it to a temporary directory. This method returns a `UUID`, that can then be used by the (slower) `ingest()` method to actually import the data. The CLI importer (described in the next section) implements this API.

## Importing using the command line interface

After cloning the The [eflips-ingest](https://github.com/mpm-tu-berlin/eflips-ingest) repository and installing its dependencies, the file `bin/ingest_vdv.py` contains a command-line interface to the importer.