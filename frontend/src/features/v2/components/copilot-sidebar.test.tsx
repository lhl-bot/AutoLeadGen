import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import CopilotSidebar from './copilot-sidebar';

describe('CopilotSidebar', () => {
  it('requires an impact preview before acknowledging a paid or mutating action', async () => {
    // GIVEN: The read-only Copilot sidebar.
    const user = userEvent.setup();
    render(<CopilotSidebar />);
    await user.click(screen.getByRole('button', { name: 'Copilot' }));

    // WHEN: The user selects a provider-backed search action.
    await user.click(screen.getByRole('button', { name: /搜索 \/ 付费补全/ }));

    // THEN: Effects and risks appear before any confirmation is possible.
    expect(screen.getByText('影响预览')).toBeInTheDocument();
    expect(screen.getByText('将调用搜索或补全 Provider')).toBeInTheDocument();
    expect(screen.getByText('当前本地阶段只允许 fake connector')).toBeInTheDocument();

    // WHEN: The preview is acknowledged.
    await user.click(screen.getByRole('button', { name: '确认预览' }));

    // THEN: The UI explicitly states that no external call or write occurred.
    expect(screen.getByText(/未执行任何外部调用或写操作/)).toBeInTheDocument();
  });

  it('moves focus into the dialog, closes on Escape, and restores focus', async () => {
    // GIVEN: Keyboard focus starts on the Copilot trigger.
    const user = userEvent.setup();
    render(<CopilotSidebar />);
    const trigger = screen.getByRole('button', { name: 'Copilot' });
    trigger.focus();

    // WHEN: The dialog opens.
    await user.click(trigger);
    const dialog = screen.getByRole('dialog', { name: 'Copilot' });
    const closeButton = within(dialog).getByRole('button', { name: '关闭 Copilot' });

    // THEN: Initial focus enters the dialog.
    await waitFor(() => expect(closeButton).toHaveFocus());

    // WHEN: The user presses Escape.
    await user.keyboard('{Escape}');

    // THEN: The dialog closes and focus returns to its trigger.
    expect(screen.queryByRole('dialog', { name: 'Copilot' })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
