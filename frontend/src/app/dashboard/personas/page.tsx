import { redirect } from 'next/navigation';

export default function LegacyPersonaRedirect() {
  redirect('/dashboard/settings/icp-playbook');
}
