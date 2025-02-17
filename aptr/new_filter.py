""" Scripts for filtering DB by PCR primers """

import pandas as pd
import numpy as np
import re
from hashlib import md5

from aptr import data_dir
from aptr.string_operations import rc, key, primers
from aptr.oor_distance import oor_distance


def _trim_primers(seq: str, left: str, right: str, reverse: bool = False, silent: bool = False) -> str:
    """
    Trim a sequence by a left and right primer.

    Args:
    -----
    seq: str
        Nucleotide sequence to trim.
    left: str
        Primer with which to trim the sequence from the 3' end.
    right: str
        Primer with which to trim the sequence from the 5' end.
    reverse: bool
        If True, reverse-complement right primer before trimming.
    silent: bool
        If True, suppress all print statements.

    Returns:
    --------
    str: trimmed sequence
    """

    if (left is None or left == "") and (right is None or right == ""):
        return seq
    elif seq is None or seq == "" or not isinstance(seq, str) or len(seq) == 0:
        if not silent:
            print(f"Warning: sequence '{seq}' is empty or not a string")
        return ""
    else:
        # Look up named primers
        left = primers[left] if left in primers else left
        right = primers[right] if right in primers else right

        # The rest is a regex operation
        fwd_primer = "".join([key[x] for x in left.lower()])
        right = rc(right) if reverse else right
        rev_primer = "".join([key[x] for x in right.lower()])
        pattern = re.compile(f"({fwd_primer}.*{rev_primer})")
        match = pattern.search(seq)
        return match.group(1) if match else ""


def filter_db(
    path_to_table: str = f"{data_dir}/patric_table.tsv.gz",
    left_primer: str = None,
    right_primer: str = None,
    silent: bool = False,
    bypass_filter=False,
) -> pd.DataFrame:
    """
    Filter DB by adapters, return candidate sequences

    Args:
    -----
    path_to_patric_table: str
        Path to the PATRIC genomic data table.
    left_primer: str
        Primer with which to trim the sequence from the 3' end.
    right_primer: str
        Primer with which to trim the sequence from the 5' end.
    silent: bool
        If True, suppress all print statements.
    bypass_filter: bool
        If True, bypass all filtering steps and return the entire table.

    Returns:
    --------
    pd.DataFrame:
        Filtered table of trimmed candidate sequences. Discards any genomes that
        no longer have two or more unique candidate sequences after trimming.
    """

    table = pd.read_table(path_to_table, dtype={"genome.genome_id": str, "feature.na_sequence": str})

    original_len = len(table)

    # Add 16S substring
    table.loc[:, "filtered_seq"] = [
        _trim_primers(x, left_primer, right_primer, silent=silent) for x in table["feature.na_sequence"]
    ]

    # Drop all bad values (may be redundant)
    table = table[table["filtered_seq"] != ""]
    table = table.dropna(subset=["filtered_seq"])
    table = table[table["filtered_seq"].str.len() > 0]

    if not silent:
        print(np.sum(table["filtered_seq"] != "") / original_len, "sequences remain after trimming")

    # Iteratively filter on sequence
    if not bypass_filter:
        diff = 1
        bad_seqs = set()
        while diff > 0:
            # Keep only features with both 16S and dnaA sequences
            # table = table.dropna(subset=["feature.na_sequence_16s", "feature.accession_dnaA"])

            # Keep only features where accessions are equal
            table = table[table["feature.accession"] == table["feature.accession.1"]]

            # Find contigs with a single sequence
            table_by_contigs = table.groupby("feature.accession.1").nunique()
            bad_contigs_idx = table_by_contigs["filtered_seq"] == 1
            bad_contigs = table_by_contigs[bad_contigs_idx]["filtered_seq"].index

            # All sequences appearing in a bad contig are bad sequences
            bad_seqs |= set(table[table["feature.accession.1"].isin(bad_contigs)]["filtered_seq"])

            # Throw out any appearances of bad sequences
            table_filtered = table[~table["filtered_seq"].isin(bad_seqs)]
            diff = len(table) - len(table_filtered)
            table = table_filtered

    if not silent:
        print(np.sum(table["filtered_seq"] != "") / original_len, "sequences remain after filtering")

    # Clean up and return table
    table = table[
        [
            "genome.genome_id",
            "genome.genome_name",
            "genome.genome_status",
            "genome.reference_genome",
            "genome.contigs",
            "feature.accession.1",
            "feature.patric_id.1",
            "feature.start.1",
            "feature.start",
            "genome.genome_length",
            "filtered_seq",
        ]
    ]
    table.columns = [
        "genome",
        "genome_name",
        "genome_status",
        "reference_genome",
        "n_contigs",
        "contig",
        "feature",
        "16s_position",
        "oor_position",
        "size",
        "16s_sequence",
    ]

    table.loc[:, "md5"] = [md5(str(x).encode("utf-8")).hexdigest() for x in table["16s_sequence"]]

    table.loc[:, "oor_distance"] = table.apply(
        lambda x: oor_distance(x["16s_position"], oor=x["oor_position"], size=x["size"])[0][0], axis=1
    )

    return table


def save_as_vsearch_db(
    db: pd.DataFrame, output_file_path: str = f"{data_dir}/vsearch_db.fa", method: str = "seq"
) -> None:
    """
    Given a dataframe of candidate sequences, save in VSEARCH-compatible format.

    Args:
    -----
    db: pd.DataFrame
        Table of candidate sequences. Output by filter_db().
    output_file_path: str
        Path to which to save the VSEARCH-compatible database.
    method: str
        Method by which to name database entries. Options are:
        - 'id': Sequences named according to their genome ID.
        - 'seq': Sequences named according to their hashed sequence.

    Returns:
    --------
    None (writes to file)
    """
    with open(output_file_path, "w+") as f:
        if method == "id":
            for _, (id, seq) in db[["feature", "16s_sequence"]].iterrows():
                print(f">{id}\n{seq}", file=f)
        elif method == "seq":
            for _, (seq, md5) in db[["16s_sequence", "md5"]].drop_duplicates().iterrows():
                print(f">{md5}\n{str(seq).lower()}", file=f)
