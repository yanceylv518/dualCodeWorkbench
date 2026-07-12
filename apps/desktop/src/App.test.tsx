import {render,screen} from '@testing-library/react';import {describe,it,expect} from 'vitest';import App from './App';
describe('workbench',()=>{it('renders core collaboration surfaces',()=>{render(<App/>);expect(screen.getAllByText('DualCode Workbench').length).toBeGreaterThan(0);expect(screen.getByText('Claude 规划')).toBeTruthy();expect(screen.getByText('Git Diff')).toBeTruthy()})});
