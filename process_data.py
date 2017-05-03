#!/usr/bin/env python

"""Process all lexirumah data into a database.

Take all the data and metadata in `datasets/`, and if possible
generate a lexirumah sqlite from it.

"""

import os
import argparse
import json
import pandas
import sys

try:
    from nameparser import HumanName
except ImportError:
    # Construct a dummy HumanName parser.
    class HumanName:
        """A very dumb human name parser."""

        def __init__(self, name):
            """Create a HumanName object from a name string.

            Assume that first and last name split at the first ' ', or
            that everything is last name if there is no space.

            """
            if " " in name:
                self.first = name[:name.index(" ")].strip()
                self.last = name[name.index(" "):].strip()
            else:
                self.last = name
                self.first = ""

try:
    # Attempt to load enoug Lexirumah to construct an SQLite database for it.
    from clld.db.meta import DBSession
    from clld.db.models import common
    Dataset = common.Dataset
    Editor = common.Editor
    Contributor = common.Contributor
    ContributionContributor = common.ContributionContributor
    ValueSet = common.ValueSet
    from lexibank.models import (
        LexibankLanguage, Concept, Provider, Counterpart,
        CognatesetCounterpart, Cognateset)
    from clld_glottologfamily_plugin.models import Family
    model_is_available = True

except ImportError:
    # We could not get Lexibank to load, create some dummy classes to
    # test at least some relations between the data.
    class DummyDBSession:
        """A dummy DBSession class.

        It implements `.add()` with the same function profile as
        DBSession, doing nothing.

        """

        def add(self, data):
            """Do Nothing."""
            pass

    DBSession = DummyDBSession()

    class Ignore:
        """A dummy SQLAlchemy-like class.

        It just ignores whatever is fed to its constructor.

        """

        def __init__(self, *args, **kwargs):
            """Ignore everything."""
            pass
    Dataset = Editor = Contributor = ContributionContributor = ValueSet = (
        Ignore)
    LexibankLanguage = Concept = Provider = Counterpart = Ignore
    CognatesetCounterpart = Cognateset = Ignore
    Family = Ignore

    class Icon (Ignore):
        """A dummy Icon class.

        Icons are expected to have a name attribute.

        """

        name = None

    model_is_available = False


# Utility functions
def report(problem, *args, process_log=None):
    """Write a problem report to a log file.

    There is probably a `logging` module that does this better.

    """
    process_log = open(process_log, 'a') if process_log else sys.stdout
    process_log.write(problem)
    for arg in args:
        process_log.write("\n  ")
        process_log.write(arg)
    process_log.write("\n")


# Define some paths.
concepticon_path = "concepts.tsv"
languages_path = "languages.tsv"
process_log = None


def import_concepticon(concepticon_path=concepticon_path):
    """Load data from concepticon tsv.

    Load the TSV file passed as argument, and put the corresponding
    Concept objects in the database, linking them from a column in the
    resulting pandas.DataFrame.

    """
    concepticon = pandas.io.parsers.read_csv(
        concepticon_path,
        sep='\t',
        index_col="Concept ID",
        na_values=[""],
        keep_default_na=False,
        encoding='utf-8')
    concepticon = concepticon.groupby(level=0).last()
    concepticon["db_Object"] = [
        Concept(
            id=str(i),
            # GLOSS
            name=row['English'],
            concepticon_id=row.get('CONCEPTICON_ID', 0),
            semanticfield="")
        for i, row in concepticon.iterrows()]
    return concepticon


def import_languages(languages_path=languages_path):
    """Load language metadata from languages tsv.

    Load the TSV file passed as argument, and put the corresponding
    LexibankLanguage objects in the database, linking them from a column in the
    resulting pandas.DataFrame.

    """
    languages = pandas.io.parsers.read_csv(
        languages_path,
        sep='\t',
        index_col="Language ID",
        na_values=[""],
        keep_default_na=False,
        encoding='utf-8')
    families = {
        family: Family(
            id=family.lower(),
            jsondata={"icon": icon},
            name=family)
        for icon, family in zip(
                ["fffffff", "ccccccc"],
                set(languages["Family"]))
        }
    languages["db_Object"] = [
        LexibankLanguage(
            id=i,
            name=row['Language name (-dialect)'],
            latitude=row['Lat'],
            family=families[row['Family']],
            longitude=row['Lon'])
        for i, row in languages.iterrows()]
    return languages


def import_contribution_metadata(
        mdpath,
        contributors={},
        trust=[]):
    """Load the metadata corresponding to a contribution.

    `trust` is a list of filenames we have to assume to be correct,
    and are not permitted to write back to.  All other files may be
    updated.

    """
    with open(mdpath) as mdfile:
        md = json.load(mdfile)

    try:
        md["abstract"]
    except KeyError:
        md["abstract"] = "[No description]"

    default_name = os.path.split(mdpath)[-1][:-len(".tsv-metadata.json")]
    contrib = Provider(
        id=md.get("id", default_name),
        # The idea is the filename without extension
        name=md.get("name", default_name),
        # The name is the filename with extension
        # references_text=str(md.get("source", []) +
        #                     md.get("references", [])),
        # We expect "source" to stand for primary linguistic data
        # (audio files etc.), and "references" to point to
        # bibliographic data. TODO: But that's a thing we will sort
        # out at a later stage.
        )
    # Provider also has attributes url, aboutUrl, language_count,
    # parameter_count, lexeme_count, synonym

    contributor_name = HumanName(md.get("creator", "?")[0])
    contributor_id = (contributor_name.last + contributor_name.first)
    try:
        contributor = contributors[contributor_id]
    except KeyError:
        contributors[contributor_id] = contributor = Contributor(
            id=contributor_id,
            name=str(contributor_name))
    DBSession.add(ContributionContributor(contribution=contrib,
                                          contributor=contributor))

    # Write back normalized metadata, if permitted.
    if mdpath not in trust:
        with open(mdpath, "w") as mdfile:
            json.dump(md, mdfile, indent=2, sort_keys=True)

    return contrib


def get_language(language, language_name, languages):
    """Try to find a language in a dataframe of languages.

    Try to look up language (by ID) or language_name (by name) in the
    DataFrame languages. Raise ValueError if the ID is invalid and the
    name is NULL, and KeyError if the neither name nor id can be
    found.

    """
    if language not in languages.index:
        # Try to look up language by name instead
        if pandas.isnull(language_name):
            raise ValueError
        else:
            get_entries = languages[
                "Language name (-dialect)"] == language_name
            if get_entries.any():
                language = get_entries.argmax()
            else:
                raise KeyError
    return language


def get_feature(concept_id, english, features):
    """Look concept up in dataframe of concepts.
    
    Try to find the described concept in our concepticon, first by id,
    then by English gloss.

    """
    # Transform the concept_id into an integer
    if type(concept_id) == float and not pandas.isnull(concept_id):
        concept_id = int(concept_id)

    if pandas.isnull(concept_id) or concept_id not in features.index:
        # Lookup by ID failed
        if pandas.isnull(english) or english not in features["English"].values:
            to_english = "to {:}".format(english)
            if to_english not in features["English"].values:
                report(
                    "Concept_Id not set, and unable to reconstruct",
                    concept_id,
                    english)
                new_concept = {}
                for c_column in features.columns:
                    new_concept.setdefault(c_column, None)
                features.loc[features.index.max() + 1] = new_concept
                concept_id = (features["English"] == to_english).argmax()
                print("Concept_Id {:s} created in {:d}".format(english, concept_id))
            else:
                concept_id = (features["English"] == to_english).argmax()
        else:
            concept_id = (features["English"] == english).argmax()
    return concept_id


def write_normalized_data(data, path):
    """Write the wordlist to file.

    After sorting rows and columns, write the word list DataFrame
    `data` to the file specified by `path`.  If path ends with `csv`,
    write in CSV format, otherwise write in TSV format.

    """
    data = data.reset_index()
    data.sort_values(by=["Feature_ID", "Family", "Region"], inplace=True)
    first_columns = [
        "Feature_ID",
        "English",
        "Language_ID",
        "Language name (-dialect)",
        "Family",
        "Region",
        "Value",
        "Comment",
        "Alignment",
        "Cognate Set",
        "Source"]
    for column in data.columns:
        if column not in first_columns + ['index']:
            first_columns.append(column)
    data = data[first_columns]
    data.to_csv(
        path,
        index=False,
        sep="," if path.endswith(".csv") else "\t",
        na_rep="",
        encoding='utf-8')


copy_from_concepticon = ["English"]
copy_from_languages = ["Family", "Region", "Language name (-dialect)"]
make_sure_exists = [
    "Alignment", "Cognate Set", "Source", "Comment", "Language_ID"]


def import_contribution(
        path,
        concepticon,
        languages,
        contributors={},
        trust=[],
        valuesets={},
        values={},
        cognatesets={}):
    """Load a word list from a file.

    Import a contribution (tsv dataset and its metadata file)
    corresponding to one word list (may contain several languages)
    from `path`.

    `trust` is a list of filenames we have to assume to be correct,
    and are not permitted to write back to.  All other files may be
    updated.

    """
    mdpath = path + '-metadata.json'
    contrib = import_contribution_metadata(mdpath, contributors, trust)

    # Open the data frame and to some initial clean up.
    data = pandas.io.parsers.read_csv(
        path,
        sep="," if path.endswith(".csv") else "\t",
        na_values=[""],
        keep_default_na=False,
        encoding='utf-8')

    if 'keraf/' in path:
        # The Keraf contributions may have strange column names.
        try:
            data.columns = ["Language name (-dialect)",
                            "Language_ID",
                            "Indonesian",
                            "Value",
                            "English",
                            "Comment"]
            data["Feature_ID"] = None
        except ValueError:
            pass

    for column in (make_sure_exists +
                   copy_from_concepticon +
                   copy_from_languages):
        if column not in data.columns:
            data[column] = ""
        data[column] = data[column].astype(str)

    # Import all the rows.
    for i, row in data.iterrows():
        # Try to find the language in the list. If not found, log a
        # message and take on the next row.
        try:
            language = get_language(
                row["Language_ID"], row.get("Language name (-dialect)"),
                languages)
        except ValueError:
            report(
                "No language given!",
                "Language in row {:d} had invalid id {:}.".format(
                    -1, language), "No name was given either. Ignored.")
            continue
        except KeyError:
            report(
                "Invalid language given!",
                "Language in row {:d} had invalid id {:}.".format(
                    -1, language),
                "The name {:s} could not be found either.".format(
                    row.get("Language name (-dialect)")), "Ignored.")
            continue
        data.set_value(i, "Language_ID", language)
        # Copy redundant columns.
        for column in copy_from_languages:
            if row[column] != languages[column][language]:
                data.set_value(i, column, languages[column][language])

        # Try to find the feature in the list. If not found, log a
        # message and take on the next row.
        try:
            feature = get_feature(
                row["Feature_ID"], row["English"].strip().lower(),
                concepticon)
        except ValueError:
            continue
        data.set_value(i, "Feature_ID", feature)
        for column in copy_from_concepticon:
            if row[column] != concepticon[column][feature]:
                data.set_value(i, column, concepticon[column][feature])

        # Create the objects representing the form in the
        # database. This is a value in a value set.
        value = row["Value"]
        if pandas.isnull(value):
            alignment = row["Alignment"]
            if pandas.isnull(alignment):
                report("Value not given, and unable to reconstruct",
                       value,
                       alignment)
            else:
                value = "".join(alignment.split())

        vsid = "{:s}-{:}".format(language, feature)
        if feature in valuesets:
            vs = valuesets[vsid]
        else:
            vs = valuesets[vsid] = ValueSet(
                vsid,
                parameter=concepticon["db_Object"][feature],
                language=languages["db_Object"][language],
                contribution=contrib,
                source=row['Source'])
        vid = "{:s}-{:}-{:}".format(language, feature, value)
        if vid not in values:
            value = values[vid] = Counterpart(
                id=vid,
                valueset=vs,
                name=value)
            DBSession.add(value)
        else:
            value = values[vid]

        if ((row["Cognate Set"] and
             not pandas.isnull(row["Cognate Set"]) and
             row["Cognate Set"] != "nan")):
            for cognate in [row["Cognate Set"]]:
                if type(cognate) == float:
                    cognate = int(cognate)
                elif type(cognate) == int:
                    pass
                else:
                    cognateset_id = hash(cognate)
                try:
                    cognateset = cognatesets[cognateset_id]
                except KeyError:
                    cognateset = cognatesets[cognateset_id] = Cognateset(
                        id=cognateset_id,
                        contribution=contrib,
                        name=cognate)
                    print("Created cognate class", cognate)
                DBSession.add(
                    CognatesetCounterpart(
                        cognateset=cognateset,
                        counterpart=value))

    if path not in trust:
        write_normalized_data(data, path)
    return data


def import_cldf(srcdir, concepticon, languages, trust=[]):
    """Import all data sets below a directory.

    Recurse through `scrdir` and import every contribution
    encountered.

    """
    all_data = pandas.DataFrame()
    for dirpath, dnames, fnames in os.walk(srcdir):
        for fname in fnames:
            if os.path.splitext(fname)[1] in ['.tsv', '.csv']:
                print("Importing {:s}…".format(os.path.join(dirpath, fname)))
                data = import_contribution(
                    os.path.join(dirpath, fname),
                    concepticon,
                    languages,
                    trust=trust)
                data["Source"] = os.path.join(dirpath, fname)
                all_data = pandas.concat((all_data, data))
                print("Import done.")
    if "all_data.tsv" not in trust:
        all_data.sort_values(
            by=["Feature_ID",
                "Family",
                "Region",
                "Language name (-dialect)"]).to_csv(
                    "all_data.tsv",
                    index=False,
                    sep="\t",
                    na_rep="",
                    encoding='utf-8')
    return all_data


def db_main(trust=[languages_path, concepticon_path]):
    """Build the database.

    Prepare the database, construct a main contribution, then import
    all data sets in "datasets/".

    """
    with open("metadata.json") as md:
        dataset_metadata = json.load(md)

    ds = Dataset(
        id=dataset_metadata["id"],
        name=dataset_metadata["name"],
        publisher_name=dataset_metadata["publisher_name"],
        publisher_place=dataset_metadata["publisher_place"],
        publisher_url=dataset_metadata["publisher_url"],
        license=dataset_metadata["license"],
        domain=dataset_metadata["domain"],
        contact=dataset_metadata["contact"],
        jsondata={
            'license_icon': dataset_metadata["license_icon"],
            'license_name': dataset_metadata["license_name"]})
    DBSession.add(ds)

    contributors = {}
    primary = True
    for i, editor in enumerate(dataset_metadata["editors"]):
        if not editor:
            primary = False
            continue
        contributor_name = HumanName(editor)
        contributor_id = ("ED" + contributor_name.last + contributor_name.first)
        # FIXME: Don't use ID hack, instead hand contributors dict
        # through.
        try:
            contributor = contributors[contributor_id]
        except KeyError:
            contributors[contributor_id] = contributor = Contributor(
                id=contributor_id,
                name=str(contributor_name))
        DBSession.add(Editor(dataset=ds, contributor=contributor,
                             ord=i, primary=primary))

    concepticon = import_concepticon()
    languages = import_languages()
    import_cldf("datasets", concepticon, languages, trust=trust)
    if languages_path not in trust:
        languages.to_csv(
            languages_path,
            sep='\t',
            na_rep="",
            encoding='utf-8')
    if concepticon_path not in trust:
        concepticon.to_csv(
            concepticon_path,
            sep='\t',
            na_rep="",
                encoding='utf-8')


def main():
    import lexibank
    sys.argv=["i", os.path.join(os.path.dirname(os.path.dirname(lexibank.__file__)), "development.ini")]

    if model_is_available:
            from clld.scripts.util import initializedb
            from clld.db.util import compute_language_sources
            try:
                initializedb(create=db_main, prime_cache=lambda x: None)
            except SystemExit:
                print("done")
    else:
            parser = argparse.ArgumentParser(description="Process LexiRumah data with consistency in mind")
            parser.add_argument("--sqlite", default=None, const="gramrumah.sqlite", nargs="?",
                                help="Generate an sqlite database from the data")
            parser.add_argument("--trust", "-t", nargs="*", type=argparse.FileType("r"), default=[],
                                help="Data files to be trusted in case of mismatch")
            #args = parser.parse_args()
            #main([x.name for x in args.trust])
            main([languages_path, concepticon_path])


if __name__ == '__main__':
    main()
