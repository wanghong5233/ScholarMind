# Contributing to ScholarMind

First off, thank you for considering contributing to ScholarMind! It's people like you that make open source such a great community.

## Where do I go from here?

If you've noticed a bug or have a feature request, [make one](https://github.com/your-username/ScholarMind/issues/new)! It's generally best if you get confirmation of your bug or approval for your feature request this way before starting to code.

### Fork & create a branch

If this is something you think you can fix, then fork ScholarMind and create a branch with a descriptive name.

A good branch name would be (where issue #325 is the ticket you're working on):

```sh
git checkout -b 325-add-new-parser-for-epub
```

### Get the test suite running

Make sure you can get the test suite running on your local machine.

### Implement your fix or feature

At this point, you're ready to make your changes! Feel free to ask for help; everyone is a beginner at first 😸

### Make a Pull Request

At this point, you should switch back to your master branch and make sure it's up to date with ScholarMind's master branch:

```sh
git remote add upstream git@github.com:your-username/ScholarMind.git
git checkout master
git pull upstream master
```

Then update your feature branch from your local copy of master, and push it!

```sh
git checkout 325-add-new-parser-for-epub
git rebase master
git push --force origin 325-add-new-parser-for-epub
```

Finally, go to GitHub and make a Pull Request.

### Keeping your Pull Request updated

If a maintainer asks you to "rebase" your PR, they're saying that a lot of code has changed, and that you need to update your branch so it's easier to merge.

To learn more about rebasing and merging, check out this guide on [syncing a fork](https://help.github.com/articles/syncing-a-fork).

---

Thank you for your contribution!
