%global pkg_version %{?pkg_version}%{!?pkg_version:1.0.0}
%global pkg_release %{?pkg_release}%{!?pkg_release:1}

Name:           slurm-quota
Version:        %{pkg_version}
Release:        %{pkg_release}%{?dist}
Summary:        Slurm quota management tool

License:        MIT
URL:            https://github.com/rackslab/slurm-quota
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  asciidoctor
BuildRequires:  python3-devel
BuildRequires:  python3dist(pytest)
BuildRequires:  systemd-rpm-macros

Requires:       python3

%description
slurm-quota assigns CPU/GPU minute quotas to Slurm users and accounts and
enforces them on submission and completion paths.

%package controller
Summary:        Controller-side files for slurm-quota
Requires:       %{name} = %{version}-%{release}
Requires:       lua-dbi
Requires:       lua-posix
Requires:       sqlite
Requires(post): /usr/bin/python3

%description controller
Controller package for slurm-quota with Slurm integration files, systemd units,
logrotate policy and the migration helper.

%prep
%autosetup

%build
# Generate man page from source AsciiDoc file.
asciidoctor -b manpage -o slurm-quota.1 man/slurm-quota.1.adoc

%check
cd %{_builddir}/%{buildsubdir}
TEST_USER=slurmquota_test
if ! id -u "${TEST_USER}" >/dev/null 2>&1; then
  useradd -r -M -s /sbin/nologin "${TEST_USER}" >/dev/null 2>&1 || true
fi
if id -u "${TEST_USER}" >/dev/null 2>&1; then
  su -s /bin/bash -c 'PYTHONPATH=%{_builddir}/%{buildsubdir} %pytest -q --override-ini="addopts="' "${TEST_USER}"
else
  PYTHONPATH=%{_builddir}/%{buildsubdir} %pytest -q --override-ini="addopts="
fi

%install
install -Dm0755 slurm-quota %{buildroot}%{_bindir}/slurm-quota
install -Dm0644 slurm-quota.bash-completion %{buildroot}%{_sysconfdir}/bash_completion.d/slurm-quota
install -Dm0644 slurm-quota.1 %{buildroot}%{_mandir}/man1/slurm-quota.1
install -Dm0755 slurm-quota-charge-wrapper %{buildroot}%{_sysconfdir}/slurm/slurm-quota-charge-wrapper
install -Dm0644 job_submit.lua %{buildroot}%{_sysconfdir}/slurm/job_submit.lua
install -Dm0644 slurm-quota.service %{buildroot}%{_unitdir}/slurm-quota.service
install -Dm0644 slurm-quota.socket %{buildroot}%{_unitdir}/slurm-quota.socket
install -Dm0644 slurm-quota-charge.logrotate %{buildroot}%{_sysconfdir}/logrotate.d/slurm-quota-charge
install -Dm0755 migrate-slurm-quota %{buildroot}%{_libexecdir}/slurm-quota/migrate-slurm-quota
sed -i 's|/usr/local/bin/slurm-quota|%{_bindir}/slurm-quota|g' %{buildroot}%{_sysconfdir}/slurm/slurm-quota-charge-wrapper
sed -i 's|/usr/local/bin/slurm-quota|%{_bindir}/slurm-quota|g' %{buildroot}%{_unitdir}/slurm-quota.service

%post controller
DB_PATH=/var/lib/state/slurm-quota/slurm-quota.db
MIGRATE=%{_libexecdir}/slurm-quota/migrate-slurm-quota

if [ -x "${MIGRATE}" ] && [ -f "${DB_PATH}" ]; then
    "${MIGRATE}" || exit 1
fi

%files
%license LICENSE
%{_bindir}/slurm-quota
%{_sysconfdir}/bash_completion.d/slurm-quota
%{_mandir}/man1/slurm-quota.1*

%files controller
%config(noreplace) %{_sysconfdir}/slurm/slurm-quota-charge-wrapper
%config(noreplace) %{_sysconfdir}/slurm/job_submit.lua
%{_unitdir}/slurm-quota.service
%{_unitdir}/slurm-quota.socket
%config(noreplace) %{_sysconfdir}/logrotate.d/slurm-quota-charge
%{_libexecdir}/slurm-quota/migrate-slurm-quota
