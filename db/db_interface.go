package db

import (
	"github.com/vpnda/sandwich-sync/pkg/models"
)

// DBInterface defines the interface for database operations
type DBInterface interface {
	Initialize() error
	Close() error
	GetTransactions() ([]*models.TransactionWithAccount, error)
	GetTransactionByReference(referenceNumber string) (*models.TransactionWithAccount, error)
	SaveTransaction(tx *models.TransactionWithAccount) error
	UpdateTransaction(tx *models.TransactionWithAccount) error
	RemoveTransaction(referenceNumber string) error
	AddManualTransaction(tx *models.TransactionWithAccount) error

	UpsertAccountMapping(am *models.AccountMapping) error
	GetAccountMapping(externalId string) (*models.AccountMapping, error)

	GetAccounts() ([]models.LunchMoneyAccount, error)
	UpsertAccountBalance(externalAccountName string, balance models.Amount) error
	DisableSyncOptions(lunchMoneyId string, syncOption models.SyncOption) error
	IsSyncOptionEnabled(lunchMoneyId int64, syncOption models.SyncOption) (bool, error)
}

// Ensure DB implements DBInterface
var _ DBInterface = (*DB)(nil)

// Ensure MockDB implements DBInterface
var _ DBInterface = (*MockDB)(nil)
