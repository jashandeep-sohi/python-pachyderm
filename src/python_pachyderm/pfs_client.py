# -*- coding: utf-8 -*-

import collections
import itertools
from contextlib import contextmanager

from python_pachyderm.client.pfs import pfs_pb2 as proto
from python_pachyderm.client.pfs import pfs_pb2_grpc as grpc
from python_pachyderm.util import commit_from, get_address, get_metadata

BUFFER_SIZE = 3 * 1024 * 1024  # 3MB TODO: Base this on some grpc value


class PfsClient(object):
    def __init__(self, host=None, port=None, auth_token=None, root_certs=None):
        """
        Creates a client to connect to PFS.

        Params:
        * host: The pachd host. Default is 'localhost', which is used with
        `pachctl port-forward`.
        * port: The port to connect to. Default is 30650.
        * auth_token: The authentication token; used if authentication is
        enabled on the cluster. Default to `None`.
        * root_certs:  The PEM-encoded root certificates as byte string.
        """

        address = get_address(host, port)
        self.metadata = get_metadata(auth_token)
        if root_certs:
            ssl_channel_credentials = grpc.grpc.ssl_channel_credentials
            ssl = ssl_channel_credentials(root_certificates=root_certs)
            self.channel = grpc.grpc.secure_channel(address, ssl)
        else:
            self.channel = grpc.grpc.insecure_channel(address)
        self.stub = grpc.APIStub(self.channel)

    def create_repo(self, repo_name, description=None, update=None):
        """
        Creates a new `Repo` object in PFS with the given name. Repos are the
        top level data object in PFS and should be used to store data of a
        similar type. For example rather than having a single `Repo` for an
        entire project you might have separate `Repo`s for logs, metrics,
        database dumps etc.

        Params:
        * repo_name: Name of the repo.
        * description: An optional string describing the repo.
        * update: Whether to update if the repo already exists.
        """
        req = proto.CreateRepoRequest(
            repo=proto.Repo(name=repo_name),
            description=description,
            update=update
        )
        self.stub.CreateRepo(req, metadata=self.metadata)

    def inspect_repo(self, repo_name):
        """
        Returns info about a specific repo. Returns a `RepoInfo` object.

        Params:
        * repo_name: Name of the repo.
        """
        req = proto.InspectRepoRequest(repo=proto.Repo(name=repo_name))
        return self.stub.InspectRepo(req, metadata=self.metadata)

    def list_repo(self):
        """
        Returns info about all repos, as a list of `RepoInfo` objects.
        """
        req = proto.ListRepoRequest()
        res = self.stub.ListRepo(req, metadata=self.metadata)
        return res.repo_info

    def delete_repo(self, repo_name, force=None):
        """
        Deletes a repo and reclaims the storage space it was using.

        Params:
        * repo_name: The name of the repo.
        * force: If set to true, the repo will be removed regardless of
        errors. This argument should be used with care.
        """
        req = proto.DeleteRepoRequest(repo=proto.Repo(name=repo_name), force=force, all=False)
        self.stub.DeleteRepo(req, metadata=self.metadata)

    def delete_all_repos(self, force=None):
        """
        Deletes all repos.

        Params:
        * force: If set to true, the repo will be removed regardless of
        errors. This argument should be used with care.
        """

        req = proto.DeleteRepoRequest(force=force, all=True)
        self.stub.DeleteRepo(req, metadata=self.metadata)

    def start_commit(self, repo_name, branch=None, parent=None, description=None, provenance=None):
        """
        Begins the process of committing data to a Repo. Once started you can
        write to the Commit with PutFile and when all the data has been
        written you must finish the Commit with FinishCommit. NOTE, data is
        not persisted until FinishCommit is called. A Commit object is
        returned.

        Params:
        * repo_name: A string specifying the name of the repo.
        * branch: A string specifying the branch name. This is a more
        convenient way to build linear chains of commits. When a commit is
        started with a non-empty branch the value of branch becomes an alias
        for the created Commit. This enables a more intuitive access pattern.
        When the commit is started on a branch the previous head of the branch
        is used as the parent of the commit.
        * parent: An optional `Commit` object specifying the parent commit.
        Upon creation the new commit will appear identical to the parent
        commit, data can safely be added to the new commit without affecting
        the contents of the parent commit.
        * description: An optional string describing the commit.
        * provenance: An optional iterable of `CommitProvenance` objects
        specifying the commit provenance.
        """
        req = proto.StartCommitRequest(
            parent=proto.Commit(repo=proto.Repo(name=repo_name), id=parent),
            branch=branch,
            description=description,
            provenance=provenance,
        )
        return self.stub.StartCommit(req, metadata=self.metadata)

    def finish_commit(self, commit, description=None,
                      tree_object_hashes=None, datum_object_hash=None,
                      size_bytes=None, empty=None):
        """
        Ends the process of committing data to a Repo and persists the
        Commit. Once a Commit is finished the data becomes immutable and
        future attempts to write to it with PutFile will error.

        Params:
        * commit: A tuple, string, or `Commit` object representing the commit.
        * description: An optional string describing this commit.
        * tree_object_hashes: A list of zero or more strings specifying object
        hashes.
        * datum_object_hash: An optional string specifying an object hash.
        * size_bytes: An optional int.
        * empty: An optional bool. If set, the commit will be closed (its
        `finished` field will be set to the current time) but its `tree` will
        be left nil.
        """
        req = proto.FinishCommitRequest(
            commit=commit_from(commit),
            description=description,
            trees=[proto.Object(hash=h) for h in tree_object_hashes] if tree_object_hashes is not None else None,
            datums=proto.Object(hash=datum_object_hash) if datum_object_hash is not None else None,
            size_bytes=size_bytes,
            empty=empty,
        )
        return self.stub.FinishCommit(req, metadata=self.metadata)

    @contextmanager
    def commit(self, repo_name, branch=None, parent=None, description=None):
        """
        A context manager for running operations within a commit.

        Params:
        * repo_name: A string specifying the name of the repo.
        * branch: A string specifying the branch name. This is a more
        convenient way to build linear chains of commits. When a commit is
        started with a non-empty branch the value of branch becomes an alias
        for the created Commit. This enables a more intuitive access pattern.
        When the commit is started on a branch the previous head of the branch
        is used as the parent of the commit.
        * parent: An optional `Commit` object specifying the parent commit.
        Upon creation the new commit will appear identical to the parent
        commit, data can safely be added to the new commit without affecting
        the contents of the parent commit.
        * description: An optional string describing the commit.
        """
        commit = self.start_commit(repo_name, branch, parent, description)
        try:
            yield commit
        finally:
            self.finish_commit(commit)

    def inspect_commit(self, commit, block_state=None):
        """
        Inspects a commit. Returns a `CommitInfo` object.

        Params:
        * commit: A tuple, string, or `Commit` object representing the commit.
        * block_state: Causes inspect commit to block until the commit is in
        the desired commit state.
        """
        req = proto.InspectCommitRequest(commit=commit_from(commit), block_state=block_state)
        return self.stub.InspectCommit(req, metadata=self.metadata)

    def list_commit(self, repo_name, to_commit=None, from_commit=None, number=None):
        """
        Lists commits. Yields `CommitInfo` objects.

        Params:
        * repo_name: If only `repo_name` is given, all commits in the repo are
        returned.
        * to_commit: Optional. Only the ancestors of `to`, including `to`
        itself, are considered.
        * from_commit: Optional. Only the descendants of `from`, including
        `from` itself, are considered.
        * number: Optional. Determines how many commits are returned.  If
        `number` is 0, all commits that match the aforementioned criteria are
        returned.
        """
        req = proto.ListCommitRequest(repo=proto.Repo(name=repo_name), number=number)
        if to_commit is not None:
            req.to.CopyFrom(commit_from(to_commit))
        if from_commit is not None:
            getattr(req, 'from').CopyFrom(commit_from(from_commit))
        return self.stub.ListCommitStream(req, metadata=self.metadata)

    def delete_commit(self, commit):
        """
        Deletes a commit.

        Params:
        * commit: A tuple, string, or `Commit` object representing the commit.
        """
        req = proto.DeleteCommitRequest(commit=commit_from(commit))
        self.stub.DeleteCommit(req, metadata=self.metadata)

    def flush_commit(self, commits, repos=None):
        """
        Blocks until all of the commits which have a set of commits as
        provenance have finished. For commits to be considered they must have
        all of the specified commits as provenance. This in effect waits for
        all of the jobs that are triggered by a set of commits to complete.
        It returns an error if any of the commits it's waiting on are
        cancelled due to one of the jobs encountering an error during runtime.
        Note that it's never necessary to call FlushCommit to run jobs,
        they'll run no matter what, FlushCommit just allows you to wait for
        them to complete and see their output once they do. This returns an
        iterator of CommitInfo objects.

        Yields `CommitInfo` objects.

        Params:
        * commits: A list of tuples, strings, or `Commit` objects representing
        the commits to flush.
        * repos: An optional list of strings specifying repo names. If
        specified, only commits within these repos will be flushed.
        """
        to_repos = [proto.Repo(name=r) for r in repos] if repos is not None else None
        req = proto.FlushCommitRequest(commits=[commit_from(c) for c in commits],
                                       to_repos=to_repos)
        return self.stub.FlushCommit(req, metadata=self.metadata)

    def subscribe_commit(self, repo_name, branch, from_commit_id=None, state=None):
        """
        Yields `CommitInfo` objects as commits occur.

        Params:
        * repo_name: A string specifying the name of the repo.
        * branch: A string specifying branch to subscribe to.
        * from_commit_id: An optional string specifying the commit ID. Only
        commits created since this commit are returned.
        * state: The commit state to filter on.
        """
        repo = proto.Repo(name=repo_name)
        req = proto.SubscribeCommitRequest(repo=repo, branch=branch, state=state)
        if from_commit_id is not None:
            getattr(req, 'from').CopyFrom(proto.Commit(repo=repo, id=from_commit_id))
        return self.stub.SubscribeCommit(req, metadata=self.metadata)

    def create_branch(self, repo_name, branch_name, commit=None, provenance=None):
        """
        Creates a new branch.

        Params:
        * repo_name: A string specifying the name of the repo.
        * branch_name: A string specifying the new branch name.
        * commit: An optional tuple, string, or `Commit` object representing
        the head commit of the branch.
        * provenance: An optional iterable of `Branch` objects representing
        the branch provenance.
        """
        req = proto.CreateBranchRequest(
            branch=proto.Branch(repo=proto.Repo(name=repo_name), name=branch_name),
            head=commit_from(commit) if commit is not None else None,
            provenance=provenance,
        )
        self.stub.CreateBranch(req, metadata=self.metadata)

    def inspect_branch(self, repo_name, branch_name):
        """
        Inspects a branch. Returns a `BranchInfo` object.
        """
        branch = proto.Branch(repo=proto.Repo(name=repo_name), name=branch_name)
        req = proto.InspectBranchRequest(branch=branch)
        return self.stub.InspectBranch(req, metadata=self.metadata)

    def list_branch(self, repo_name):
        """
        Lists the active branch objects on a repo. Returns a list of
        `BranchInfo` objects.

        Params:
        * repo_name: A string specifying the repo name.
        """
        req = proto.ListBranchRequest(repo=proto.Repo(name=repo_name))
        res = self.stub.ListBranch(req, metadata=self.metadata)
        return res.branch_info

    def delete_branch(self, repo_name, branch_name, force=None):
        """
        Deletes a branch, but leaves the commits themselves intact. In other
        words, those commits can still be accessed via commit IDs and other
        branches they happen to be on.

        Params:
        * repo_name: A string specifying the repo name.
        * branch_name: A string specifying the name of the branch to delete.
        * force: A bool specifying whether to force the branch deletion.
        """
        branch = proto.Branch(repo=proto.Repo(name=repo_name), name=branch_name)
        req = proto.DeleteBranchRequest(branch=branch, force=force)
        self.stub.DeleteBranch(req, metadata=self.metadata)

    def put_file_bytes(self, commit, path, value, delimiter=None,
                       target_file_datums=None, target_file_bytes=None, overwrite_index=None):
        """
        Uploads a binary bytes array as file(s) in a certain path.

        Params:
        * commit: A tuple, string, or `Commit` object representing the commit.
        * path: A string specifying the path in the repo the file(s) will be
        written to.
        * value: The file contents as bytes, represented as a file-like
        object, bytestring, or iterator of bytestrings.
        * delimiter: Optional. causes data to be broken up into separate files
        with `path` as a prefix.
        * target_file_datums: An optional int. Specifies the target number of
        datums in each written file. It may be lower if data does not split
        evenly, but will never be higher, unless the value is 0.
        * target_file_bytes: An optional int. Specifies the target number of
        bytes in each written file, files may have more or fewer bytes than
        the target.
        * overwrite_index: An optional `OverwriteIndex` object. This is the
        object index where the write starts from.  All existing objects
        starting from the index are deleted.
        """

        overwrite_index_proto = proto.OverwriteIndex(index=overwrite_index) if overwrite_index is not None else None

        if hasattr(value, "read"):
            def wrap(value):
                for i in itertools.count():
                    chunk = value.read(BUFFER_SIZE)

                    if len(chunk) == 0:
                        return

                    if i == 0:
                        yield proto.PutFileRequest(
                            file=proto.File(commit=commit_from(commit), path=path),
                            value=chunk,
                            delimiter=delimiter,
                            target_file_datums=target_file_datums,
                            target_file_bytes=target_file_bytes,
                            overwrite_index=overwrite_index_proto
                        )
                    else:
                        yield proto.PutFileRequest(value=chunk)
        elif isinstance(value, collections.Iterable) and not isinstance(value, (str, bytes)):
            def wrap(value):
                for i, chunk in enumerate(value):
                    if i == 0:
                        yield proto.PutFileRequest(
                            file=proto.File(commit=commit_from(commit), path=path),
                            value=chunk,
                            delimiter=delimiter,
                            target_file_datums=target_file_datums,
                            target_file_bytes=target_file_bytes,
                            overwrite_index=overwrite_index_proto
                        )
                    else:
                        yield proto.PutFileRequest(value=chunk)
        else:
            def wrap(value):
                yield proto.PutFileRequest(
                    file=proto.File(commit=commit_from(commit), path=path),
                    value=value[:BUFFER_SIZE],
                    delimiter=delimiter,
                    target_file_datums=target_file_datums,
                    target_file_bytes=target_file_bytes,
                    overwrite_index=overwrite_index_proto
                )

                for i in range(BUFFER_SIZE, len(value), BUFFER_SIZE):
                    yield proto.PutFileRequest(
                        value=value[i:i + BUFFER_SIZE],
                        overwrite_index=overwrite_index_proto
                    )

        self.stub.PutFile(wrap(value), metadata=self.metadata)

    def put_file_url(self, commit, path, url, recursive=None):
        """
        Puts a file using the content found at a URL. The URL is sent to the
        server which performs the request. Note that this is not a standard
        PFS function.

        Params:
        * commit: A tuple, string, or `Commit` object representing the commit.
        * path: A string specifying the path to the file.
        * url: A string specifying the url of the file to put.
        * recursive: allow for recursive scraping of some types URLs, for
        example on s3:// URLs.
        """
        req = iter([
            proto.PutFileRequest(
                file=proto.File(commit=commit_from(commit), path=path),
                url=url,
                recursive=recursive
            )
        ])
        self.stub.PutFile(req, metadata=self.metadata)

    def copy_file(self, source_commit, source_path, dest_commit, dest_path, overwrite=None):
        """
        Efficiently copies files already in PFS. Note that the destination
        repo cannot be an output repo, or the copy operation will (as of
        1.9.0) silently fail.

        Params:
        * source_commit: A tuple, string, or `Commit` object representing the
        commit for the source file.
        * source_path: A string specifying the path of the source file.
        * dest_commit: A tuple, string, or `Commit` object representing the
        commit for the destination file.
        * dest_path: A string specifying the path of the destination file.
        * overwrite: Am optional bool specifying whether to overwrite the
        destination file if it already exists.
        """
        req = proto.CopyFileRequest(
            src=proto.File(commit=commit_from(source_commit), path=source_path),
            dst=proto.File(commit=commit_from(dest_commit), path=dest_path),
            overwrite=overwrite,
        )
        self.stub.CopyFile(req, metadata=self.metadata)

    def get_file(self, commit, path, offset_bytes=None, size_bytes=None):
        """
        Returns an iterator of the contents of a file at a specific commit.

        Params:
        * commit: A tuple, string, or `Commit` object representing the commit.
        * path: A string specifying the path of the file.
        * offset_bytes: An optional int. Specifies a number of bytes that
        should be skipped in the beginning of the file.
        * size_bytes: An optional int. limits the total amount of data
        returned, note you will get fewer bytes than size if you pass a value
        larger than the size of the file. If size is set to 0 then all of the
        data will be returned.
        """
        req = proto.GetFileRequest(
            file=proto.File(commit=commit_from(commit), path=path),
            offset_bytes=offset_bytes,
            size_bytes=size_bytes
        )
        res = self.stub.GetFile(req, metadata=self.metadata)
        for item in res:
            yield item.value

    def inspect_file(self, commit, path):
        """
        Inspects a file. Returns a `FileInfo` object.

        Params:
        * commit: A tuple, string, or `Commit` object representing the commit.
        * path: A string specifying the path to the file.
        """
        req = proto.InspectFileRequest(file=proto.File(commit=commit_from(commit), path=path))
        return self.stub.InspectFile(req, metadata=self.metadata)

    def list_file(self, commit, path, history=None, include_contents=None):
        """
        Lists the files in a directory.

        Params:
        * commit: A tuple, string, or `Commit` object representing the commit.
        * path: The path to the directory.
        * history: An optional int that indicates to return jobs from
        historical versions of pipelines. Semantics are:
         0: Return jobs from the current version of the pipeline or pipelines.
         1: Return the above and jobs from the next most recent version
         2: etc.
        -1: Return jobs from all historical versions.
        * include_contents: An optional bool. If `True`, file contents are
        included.
        """

        req = proto.ListFileRequest(
            file=proto.File(commit=commit_from(commit), path=path),
            history=history,
            full=include_contents,
        )

        return self.stub.ListFileStream(req, metadata=self.metadata)

    def walk_file(self, commit, path):
        """
        Walks over all descendant files in a directory. Returns a generator of
        `FileInfo` objects.

        Params:
        * commit: A tuple, string, or `Commit` object representing the commit.
        * path: The path to the directory.
        """
        commit = commit_from(commit)
        f = proto.File(commit=commit_from(commit), path=path)
        req = proto.WalkFileRequest(file=f)
        return self.stub.WalkFile(req, metadata=self.metadata)

    def glob_file(self, commit, pattern):
        """
        Lists files that match a glob pattern. Yields `FileInfo` objects.

        Params:
        * commit: A tuple, string, or `Commit` object representing the commit.
        * pattern: A string representing a glob pattern.
        """

        req = proto.GlobFileRequest(commit=commit_from(commit), pattern=pattern)
        return self.stub.GlobFileStream(req, metadata=self.metadata)

    def delete_file(self, commit, path):
        """
        Deletes a file from a Commit. DeleteFile leaves a tombstone in the
        Commit, assuming the file isn't written to later attempting to get the
        file from the finished commit will result in not found error. The file
        will of course remain intact in the Commit's parent.

        Params:
        * commit: A tuple, string, or `Commit` object representing the commit.
        * path: The path to the file.
        """
        req = proto.DeleteFileRequest(file=proto.File(commit=commit_from(commit), path=path))
        self.stub.DeleteFile(req, metadata=self.metadata)

    def delete_all(self):
        """
        Deletes everything.
        """
        req = proto.google_dot_protobuf_dot_empty__pb2.Empty()
        self.stub.DeleteAll(req, metadata=self.metadata)
