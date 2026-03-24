import { useState } from "react";
import { CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Search, ArrowUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
  getFilteredRowModel,
  getSortedRowModel,
  type SortingState
} from "@tanstack/react-table";

// Define the shape of our data
export type BetRow = {
  id: number;
  market: string;
  side: "YES" | "NO";
  amount: number;
  pnl: number;
  pnlPct: number;
};

const mockBets: BetRow[] = [
  { id: 1, market: "Will interest rates drop in 2026?", side: "YES", amount: 150, pnl: 45.2, pnlPct: 30.1 },
  { id: 2, market: "Ethereum to reach $4000 by June", side: "NO", amount: 200, pnl: -12.5, pnlPct: -6.2 },
  { id: 3, market: "US GDP Growth > 2%", side: "YES", amount: 350, pnl: 10.0, pnlPct: 2.8 },
  { id: 4, market: "SpaceX Mars Mission 2029", side: "YES", amount: 500, pnl: 0, pnlPct: 0 },
  { id: 5, market: "Bitcoin to breaking $100k prior to Jan 1st", side: "NO", amount: 1200, pnl: -55.8, pnlPct: -4.6 },
];

export const columns: ColumnDef<BetRow>[] = [
  {
    accessorKey: "market",
    header: "Market",
    cell: ({ row }) => (
      <div className="font-medium max-w-[200px] lg:max-w-xs truncate pr-4" title={row.getValue("market")}>
        {row.getValue("market")}
      </div>
    ),
  },
  {
    accessorKey: "side",
    header: "Side",
    cell: ({ row }) => {
      const side = row.getValue("side") as string;
      return (
        <span className={`font-mono font-bold ${side === 'YES' ? 'text-emerald-500' : 'text-destructive'}`}>
          {side}
        </span>
      );
    },
  },
  {
    accessorKey: "amount",
    header: ({ column }) => (
      <div className="flex justify-end">
        <Button
          variant="ghost"
          onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          className="h-8 px-2 flex items-center gap-1 -mr-2 text-xs font-semibold hover:bg-muted/50 transition-colors"
        >
          Position Value
          <ArrowUpDown className="size-3.5 ml-1 text-muted-foreground" />
        </Button>
      </div>
    ),
    cell: ({ row }) => {
      const amount = parseFloat(row.getValue("amount"))
      const formatted = new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
      }).format(amount)
 
      return <div className="text-right font-mono font-medium">{formatted}</div>
    },
  },
  {
    accessorKey: "pnl",
    header: ({ column }) => (
      <div className="flex justify-end">
        <Button
          variant="ghost"
          onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          className="h-8 px-2 flex items-center gap-1 -mr-2 text-xs font-semibold hover:bg-muted/50 transition-colors"
        >
          P&L
          <ArrowUpDown className="size-3.5 ml-1 text-muted-foreground" />
        </Button>
      </div>
    ),
    cell: ({ row }) => {
      const pnl = parseFloat(row.getValue("pnl"))
      const isGain = pnl > 0;
      const isNeutral = pnl === 0;
      const formatted = new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
      }).format(Math.abs(pnl))
 
      return (
        <div className={`text-right font-mono font-bold ${isGain ? 'text-emerald-500' : isNeutral ? 'text-muted-foreground' : 'text-destructive'}`}>
          {isGain ? '+' : isNeutral ? '' : '-'}{formatted}
        </div>
      )
    },
  },
];

export function ActiveBets() {
  const [sorting, setSorting] = useState<SortingState>([])
  const [globalFilter, setGlobalFilter] = useState("")

  const table = useReactTable({
    data: mockBets,
    columns,
    getCoreRowModel: getCoreRowModel(),
    onSortingChange: setSorting,
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    state: {
      sorting,
      globalFilter,
    },
    onGlobalFilterChange: setGlobalFilter,
  })

  return (
    <>
      <CardHeader className="flex flex-col gap-4 pb-4">
        <div className="flex flex-row items-center justify-between w-full">
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-3">
              <CardTitle className="text-lg">Recent Bets</CardTitle>
              <span className="text-xs text-muted-foreground bg-secondary px-2.5 py-0.5 rounded-full font-medium">
                {table.getFilteredRowModel().rows.length} Active
              </span>
            </div>
          </div>
          <div className="relative w-[374px]">
            <Search className="absolute left-2.5 top-2 size-4 text-muted-foreground" />
            <Input 
              placeholder="Search markets..." 
              className="pl-8" 
              value={globalFilter ?? ""} 
              onChange={e => setGlobalFilter(String(e.target.value))} 
            />
          </div>
        </div>
        <Separator />
      </CardHeader>
      
      <CardContent className="flex-1 overflow-auto pt-0">
        <div className="rounded-md border border-border/50">
          <Table>
            <TableHeader className="bg-muted/50">
              {table.getHeaderGroups().map((headerGroup) => (
                <TableRow key={headerGroup.id}>
                  {headerGroup.headers.map((header) => {
                    return (
                      <TableHead key={header.id}>
                        {header.isPlaceholder
                          ? null
                          : flexRender(
                              header.column.columnDef.header,
                              header.getContext()
                            )}
                      </TableHead>
                    )
                  })}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows?.length ? (
                table.getRowModel().rows.map((row) => (
                  <TableRow
                    key={row.id}
                    data-state={row.getIsSelected() && "selected"}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell colSpan={columns.length} className="h-24 text-center">
                    No active positions matched your criteria.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </>
  );
}
