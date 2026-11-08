# Reader submodule

`mymcplusplus/` is a Git submodule of
[our fork](https://github.com/larrykoubiak/mymcplusplus), pinned to
`ded056d195728e072de94dec0b813867784630dd` on `vendor-local`. This includes
upstream 3.2.0 (`3247c9c`) and our card-validation/icon-decoder fixes.

Clone the app with `--recurse-submodules`, or initialize an existing checkout:

```bash
git submodule update --init --recursive
```

The checked-in URL uses HTTPS for portable clones and CI. This checkout keeps
Larry's SSH alias in local configuration. The submodule's `origin` is our fork;
`upstream` is Adubbz's repository with pushes disabled. Git metadata and the
original checkout's archived refs/index/diff now live in the standard local
`.git/modules/vendor/mymcplusplus/` directory. Use normal Git commands:

```bash
git -C vendor/mymcplusplus status
git -C vendor/mymcplusplus log --oneline -5
git -C vendor/mymcplusplus diff upstream/master
```

To update the dependency, work on `vendor-local`, merge the desired upstream
commit, test, and push to the fork first. Then stage/commit the new
`vendor/mymcplusplus` gitlink in the app repository. Normal submodule updates
check out the pinned commit; they do not automatically follow the branch tip.

CI initializes submodules before tests/builds. Docker and Python packaging use
the initialized source and exclude Git metadata. Built wheels/source archives
contain the required reader files, so installing an archive needs no Git fetch.
See [attribution and patches](../THIRD_PARTY.md).
